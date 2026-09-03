"use client";

import type { ReactNode } from "react";
import { AlarmClock, CircleAlert, ClipboardList, Package } from "lucide-react";

import { StateBadge } from "@/components/state-badge";
import {
  CLOSE_REASON,
  countdown,
  HINT_LABEL,
  isClosed,
  RECOVERY_ACTION,
  relativeTime,
} from "@/lib/display";
import { hintStatus } from "@/lib/policy";
import type { RunSnapshot } from "@/lib/contracts";

export function Panel({
  title,
  icon: Icon,
  action,
  children,
}: {
  title: string;
  icon?: typeof Package;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <h2 className="flex items-center gap-2 font-medium">
          {Icon ? (
            <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
          ) : null}
          {title}
        </h2>
        {action}
      </div>
      <div className="px-4 py-4">{children}</div>
    </section>
  );
}

function Line({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-[13px] text-muted-foreground">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

const PAYMENT_WORD = {
  unknown: "Not known yet",
  pending: "Pending",
  confirmed: "Confirmed",
  failed: "Failed",
} as const;

const SHIPMENT_WORD = {
  unknown: "Not known yet",
  not_created: "Not created",
  in_transit: "In transit",
  delayed: "Delayed",
  delivered: "Delivered",
} as const;

/**
 * The recorded deadline and why the run is waiting. A countdown that reaches zero says a
 * review is due; it never says one has happened, because only a recorded episode does.
 */
export function NextReviewCard({
  snapshot,
  now,
}: {
  snapshot: RunSnapshot;
  now: number;
}) {
  const closed = isClosed(snapshot.status);
  const paused = snapshot.status === "paused";
  const overdue =
    snapshot.next_wake_at !== null && Date.parse(snapshot.next_wake_at) <= now;

  return (
    <Panel title="Next review" icon={AlarmClock}>
      {closed ? (
        <div>
          <p className="font-medium">Supervision has closed.</p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            {snapshot.close_reason
              ? CLOSE_REASON[snapshot.close_reason]
              : "No further reviews will happen."}
            {snapshot.closed_at
              ? ` · ${new Date(snapshot.closed_at).toLocaleString()}`
              : ""}
          </p>
        </div>
      ) : snapshot.next_wake_at === null ? (
        <p className="text-muted-foreground">
          No review is scheduled. The run wakes on an event or an operator command.
        </p>
      ) : (
        <div>
          <p
            className="text-2xl font-semibold tracking-tight tabular-nums"
            aria-hidden="true"
          >
            {overdue ? "00:00" : countdown(snapshot.next_wake_at, now)}
          </p>
          <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
            {paused
              ? overdue
                ? "Paused · review overdue. Nothing runs until the operator resumes."
                : "Paused · this deadline is kept but not acted on."
              : overdue
                ? "Review due — waiting for the worker to process it."
                : `Due at ${new Date(snapshot.next_wake_at).toLocaleTimeString()}`}
          </p>
        </div>
      )}

      {snapshot.wake_reason && !closed ? (
        <p className="mt-3 border-t pt-3 text-[13px] leading-5">
          <span className="text-muted-foreground">Why: </span>
          {snapshot.wake_reason}
        </p>
      ) : null}

      {snapshot.wake_guidance && snapshot.wake_guidance.hints.length > 0 ? (
        <div className="mt-3 border-t pt-3">
          <p className="text-[13px] text-muted-foreground">
            The agent asked to be woken differently (v
            {snapshot.wake_guidance.version}):
          </p>
          <ul className="mt-1.5 space-y-1">
            {snapshot.wake_guidance.hints.map((hint, index) => {
              const status = hintStatus(snapshot, hint, now);
              return (
                <li key={`${hint.kind}-${index}`} className="text-[13px] leading-5">
                  <span className={status.applies ? undefined : "line-through"}>
                    {HINT_LABEL[hint.kind]}
                    {hint.event_type ? ` · ${hint.event_type}` : ""}
                    {hint.issue_id ? ` · about “${hint.issue_id}”` : ""}
                  </span>
                  {/* A hint is never withdrawn, it just stops counting. Saying so is the
                      difference between a record and a claim about current behaviour. */}
                  {status.applies ? null : (
                    <span className="text-muted-foreground">
                      {" "}
                      — no longer applied, {status.why}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {!closed ? (
        <p className="mt-3 border-t pt-3 text-[13px] leading-5 text-muted-foreground">
          Supervision ends by{" "}
          <time dateTime={snapshot.maximum_age_at}>
            {new Date(snapshot.maximum_age_at).toLocaleString()}
          </time>
          .
        </p>
      ) : null}
    </Panel>
  );
}

export function FactsCard({
  snapshot,
  now,
}: {
  snapshot: RunSnapshot;
  now: number;
}) {
  const facts = snapshot.facts;
  return (
    <Panel title="Order facts and open work" icon={Package}>
      <dl className="divide-y">
        <Line label="Payment">
          {PAYMENT_WORD[facts.payment]}
          {facts.payment_attempt_reference ? (
            <span className="block text-[13px] text-muted-foreground">
              {facts.payment_attempt_reference}
            </span>
          ) : null}
        </Line>
        <Line label="Shipment">
          {SHIPMENT_WORD[facts.shipment]}
          {facts.shipment_reference ? (
            <span className="block text-[13px] text-muted-foreground">
              {facts.shipment_reference}
            </span>
          ) : null}
        </Line>
        {facts.expected_at ? (
          <Line label="Expected">
            <time dateTime={facts.expected_at}>
              {new Date(facts.expected_at).toLocaleString()}
            </time>
          </Line>
        ) : null}
        {facts.delivered_at ? (
          <Line label="Delivered">
            <time dateTime={facts.delivered_at}>
              {new Date(facts.delivered_at).toLocaleString()}
            </time>
          </Line>
        ) : null}
        <Line label="Last progress">
          {facts.last_relevant_progress_at ? (
            <time dateTime={facts.last_relevant_progress_at}>
              {relativeTime(facts.last_relevant_progress_at, now)}
            </time>
          ) : (
            <span className="text-muted-foreground">None recorded</span>
          )}
        </Line>
      </dl>

      <div className="mt-4 border-t pt-4">
        <h3 className="text-[13px] font-medium text-muted-foreground">
          Open concerns
        </h3>
        {facts.open_issues.length === 0 ? (
          <p className="mt-2 text-muted-foreground">
            Nothing is outstanding on this order.
          </p>
        ) : (
          <ul className="mt-2 space-y-2.5">
            {facts.open_issues.map((issue) => (
              <li key={issue.issue_id}>
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium">{issue.issue_id}</span>
                  {issue.review_required ? (
                    <StateBadge
                      label="Needs review"
                      tone="hold"
                      dot={false}
                      className="px-2 py-0.5 text-[12px]"
                    />
                  ) : null}
                </div>
                <p className="mt-0.5 text-[13px] leading-5 text-muted-foreground">
                  {issue.description}
                </p>
                {issue.follow_up_at ? (
                  <p className="mt-0.5 text-[13px] text-muted-foreground">
                    Contacted already; a follow-up is reasonable after{" "}
                    {new Date(issue.follow_up_at).toLocaleTimeString()}.
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      {snapshot.deferred_evidence.length > 0 ? (
        <p className="mt-4 border-t pt-4 text-[13px] leading-5 text-muted-foreground">
          <span className="font-medium text-foreground">
            {snapshot.deferred_evidence.length} recorded input
            {snapshot.deferred_evidence.length === 1 ? "" : "s"}
          </span>{" "}
          have not been considered by a review yet. Recording something is not the
          same as looking at it.
        </p>
      ) : null}
    </Panel>
  );
}

export function RecoveryCard({ snapshot }: { snapshot: RunSnapshot }) {
  const recovery = snapshot.recovery;
  if (!recovery) return null;
  return (
    <section className="panel overflow-hidden border-destructive/30">
      <div className="flex items-center gap-2 border-b border-destructive/20 bg-alert-surface px-4 py-3">
        <CircleAlert className="size-4 text-destructive" aria-hidden="true" />
        <h2 className="font-medium text-destructive">Supervision is held up</h2>
      </div>
      <div className="px-4 py-4">
        <p className="leading-6">{recovery.reason}</p>
        <p className="mt-2 text-[13px] leading-5 text-muted-foreground">
          Everything recorded so far is kept. Resuming asks the run to{" "}
          {RECOVERY_ACTION[recovery.next_action].toLowerCase()} — it never starts
          another order.
        </p>
      </div>
    </section>
  );
}

/**
 * Instructions given to this one run. They outlive compaction and continuation, so they
 * are shown as standing text rather than folded into the narrative summary.
 */
export function InstructionListCard({
  snapshot,
  action,
  renderControls,
}: {
  snapshot: RunSnapshot;
  action?: ReactNode;
  renderControls?: (instructionId: string) => ReactNode;
}) {
  return (
    <Panel title="Standing instructions" icon={ClipboardList} action={action}>
      {snapshot.instructions.length === 0 ? (
        <p className="text-muted-foreground">
          No run-specific instruction has been added. The supervisor is following
          its template only.
        </p>
      ) : (
        <ul className="space-y-3.5">
          {snapshot.instructions.map((instruction) => (
            <li key={instruction.instruction_id}>
              <p className="leading-6">{instruction.text}</p>
              <div className="mt-1 flex flex-wrap items-center justify-between gap-2">
                <time
                  className="text-[13px] text-muted-foreground"
                  dateTime={instruction.added_at}
                >
                  Added {new Date(instruction.added_at).toLocaleString()}
                </time>
                {renderControls?.(instruction.instruction_id)}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
