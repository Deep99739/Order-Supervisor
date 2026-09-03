"use client";

import { Layers } from "lucide-react";

import { StateBadge } from "@/components/state-badge";
import { Panel } from "@/components/runs/side-panels";
import { ACTION_LABEL, RECOVERY_ACTION } from "@/lib/display";
import type { ActionName, RunSnapshot } from "@/lib/contracts";

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border px-3.5 py-3">
      <p className="text-[13px] text-muted-foreground">{label}</p>
      <p className="mt-1 font-medium tabular-nums">{value}</p>
    </div>
  );
}

/**
 * What the supervisor is carrying. The narrative, the standing instructions, the open
 * concerns, and the evidence it has not read yet are shown separately on purpose: the
 * summary explains the run, it does not establish what is true about the order.
 */
export function MemoryTab({ snapshot }: { snapshot: RunSnapshot }) {
  const memory = snapshot.memory;
  const behind = Math.max(0, snapshot.last_sequence - memory.summary_through_sequence);

  return (
    <div className="space-y-5">
      <Panel
        title="Compact narrative"
        icon={Layers}
        action={
          <StateBadge
            label={
              memory.provenance === "model"
                ? "Written by the agent"
                : "Rendered from the record"
            }
            tone={memory.provenance === "model" ? "working" : "stopped"}
            dot={false}
            className="px-2 py-0.5 text-[12px]"
          />
        }
      >
        {memory.text ? (
          <p className="leading-6 whitespace-pre-wrap">{memory.text}</p>
        ) : (
          <p className="text-muted-foreground">
            No summary has been written yet. Nothing has been lost — the full
            timeline is complete either way.
          </p>
        )}
        <div className="mt-4 grid gap-2 border-t pt-4 sm:grid-cols-3">
          <Stat label="Version" value={`v${memory.summary_version}`} />
          <Stat
            label="Covers records up to"
            value={`#${memory.summary_through_sequence}`}
          />
          <Stat
            label="Recorded since then"
            value={`${behind} record${behind === 1 ? "" : "s"}`}
          />
        </div>
        <p className="mt-3 text-[13px] leading-5 text-muted-foreground">
          This text is a summary, not the record. Anything after #
          {memory.summary_through_sequence} is outside what it describes, and the
          activity timeline remains the complete account.
        </p>
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Standing instructions">
          {snapshot.instructions.length === 0 ? (
            <p className="text-muted-foreground">
              None. Instructions added to this run survive compaction and history
              continuation.
            </p>
          ) : (
            <ul className="space-y-3">
              {snapshot.instructions.map((instruction) => (
                <li key={instruction.instruction_id} className="leading-6">
                  {instruction.text}
                  <span className="mt-0.5 block text-[13px] text-muted-foreground">
                    Added {new Date(instruction.added_at).toLocaleString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Open concerns">
          {snapshot.facts.open_issues.length === 0 ? (
            <p className="text-muted-foreground">
              Nothing is outstanding on this order.
            </p>
          ) : (
            <ul className="space-y-3">
              {snapshot.facts.open_issues.map((issue) => (
                <li key={issue.issue_id}>
                  <p className="font-medium">{issue.issue_id}</p>
                  <p className="mt-0.5 leading-6 text-muted-foreground">
                    {issue.description}
                  </p>
                  <p className="mt-0.5 text-[13px] text-muted-foreground">
                    Evidence at{" "}
                    {issue.evidence
                      .map((reference) => `#${reference.sequence}`)
                      .join(", ")}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <Panel title="Evidence not yet considered">
        {snapshot.deferred_evidence.length === 0 ? (
          <p className="text-muted-foreground">
            Every recorded input up to #{snapshot.last_decision_through_sequence}{" "}
            has been through a review.
          </p>
        ) : (
          <>
            <p className="leading-6">
              {snapshot.deferred_evidence.length} input
              {snapshot.deferred_evidence.length === 1 ? " is" : "s are"} recorded
              and waiting to be looked at. They are carried forward until a review
              genuinely covers them.
            </p>
            <p className="mt-2 text-[13px] text-muted-foreground tabular-nums">
              Records{" "}
              {snapshot.deferred_evidence
                .map((reference) => `#${reference.sequence}`)
                .join(", ")}
            </p>
          </>
        )}
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Recorded actions so far">
          {snapshot.committed_actions.length === 0 ? (
            <p className="text-muted-foreground">
              No action has been recorded for this order yet.
            </p>
          ) : (
            <ul className="space-y-3">
              {snapshot.committed_actions.map((action) => (
                <li key={action.action_id}>
                  <p className="font-medium">
                    {ACTION_LABEL[action.action as ActionName] ?? action.action}
                    <span className="ml-2 text-[13px] font-normal text-muted-foreground">
                      simulated · #{action.receipt.sequence}
                    </span>
                  </p>
                  <p className="mt-0.5 leading-6 text-muted-foreground">
                    {action.content}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Execution">
          <div className="grid gap-2 sm:grid-cols-2">
            <Stat
              label="History generation"
              value={String(snapshot.execution_generation + 1)}
            />
            <Stat
              label="Continuations"
              value={String(snapshot.counters.continuations)}
            />
            <Stat
              label="Compactions"
              value={String(snapshot.counters.compactions)}
            />
            <Stat
              label="Deferred events"
              value={String(snapshot.counters.deferred_events)}
            />
          </div>
          {/* The one identifier that is supposed to change on rollover, next to the one
              that is not. Showing them together is what makes the distinction checkable. */}
          <dl className="mt-3 space-y-2 border-t pt-3 text-[13px]">
            <div>
              <dt className="text-muted-foreground">Workflow</dt>
              <dd className="mt-0.5 font-mono text-[12px] break-all">
                {snapshot.workflow_id}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Temporal execution</dt>
              <dd className="mt-0.5 font-mono text-[12px] break-all">
                {snapshot.temporal_run_id ?? "not recorded"}
              </dd>
            </div>
          </dl>
          <p className="mt-3 text-[13px] leading-5 text-muted-foreground">
            Continuing the history is maintenance, not a new order. The execution
            above changes; the run, the workflow, and the original deadline do not.
          </p>
        </Panel>
      </div>

      {snapshot.recovery?.next_action === "consolidate_context" ? (
        <div className="panel border-destructive/30 px-4 py-4">
          <p className="font-medium text-destructive">
            The context has outgrown what one review can hold.
          </p>
          <p className="mt-1 leading-6 text-muted-foreground">
            {snapshot.recovery.reason} Nothing was dropped to make room. Replace or
            remove standing instructions to make space, then resume —{" "}
            {RECOVERY_ACTION[snapshot.recovery.next_action].toLowerCase()}.
          </p>
        </div>
      ) : null}
    </div>
  );
}
