"use client";

import { useState } from "react";
import { Check, ClipboardCopy, FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { Panel } from "@/components/runs/side-panels";
import { ACTION_LABEL, BLOCK_REASON, CLOSE_REASON, isClosed } from "@/lib/display";
import type { ActionName, FinalOutput, RunSnapshot } from "@/lib/contracts";

function asText(snapshot: RunSnapshot, report: FinalOutput): string {
  const lines = [
    `Order ${snapshot.order_id}`,
    `Closed: ${CLOSE_REASON[report.close_reason]} at ${report.closed_at}`,
    "",
    "Summary",
    report.summary,
    "",
    "Recorded actions (all simulated)",
    ...(report.important_actions.length > 0
      ? report.important_actions.map(
          (action) =>
            `- ${ACTION_LABEL[action.action as ActionName] ?? action.action}: ${action.content}`,
        )
      : ["- none"]),
    "",
    "Proposals that were refused (nothing was recorded for these)",
    ...(report.blocked_actions.length > 0
      ? report.blocked_actions.map(
          (item) =>
            `- ${ACTION_LABEL[item.action as ActionName] ?? item.action} — ${BLOCK_REASON[item.reason] ?? item.reason}: ${item.explanation}`,
        )
      : ["- none"]),
    "",
    "Unresolved concerns",
    ...(report.unresolved_issues.length > 0
      ? report.unresolved_issues.map(
          (issue) => `- ${issue.issue_id}: ${issue.description}`,
        )
      : ["- none"]),
    "",
    "Learnings",
    ...(report.learnings.length > 0
      ? report.learnings.map((item) => `- ${item}`)
      : ["- none"]),
    "",
    "Feedback",
    ...(report.feedback.length > 0
      ? report.feedback.map((item) => `- ${item}`)
      : ["- none"]),
    "",
    `Narrative: ${report.narrative_provenance === "model_assisted" ? "written by the agent over recorded facts" : "rendered from the record"}`,
    report.narrative_limitation ?? "",
    `Evidence considered through record #${report.evidence_through_sequence}`,
  ];
  return lines.join("\n");
}

function Bullets({ items, empty }: { items: string[]; empty: string }) {
  if (items.length === 0) {
    return <p className="text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="space-y-2">
      {items.map((item, index) => (
        <li key={index} className="flex gap-2.5 leading-6">
          <span aria-hidden="true" className="mt-2.5 size-1.5 shrink-0 rounded-full bg-primary/60" />
          {item}
        </li>
      ))}
    </ul>
  );
}

/**
 * Before closure this panel says a report does not exist yet. It never renders a
 * provisional "successfully completed" report, because the run has not produced one.
 */
export function OutcomeTab({ snapshot }: { snapshot: RunSnapshot }) {
  const [copied, setCopied] = useState(false);
  const report = snapshot.final_output;

  if (!report) {
    const finalizing =
      snapshot.status === "finalizing" || snapshot.pending_control === "terminate";
    return (
      <Panel title="Final report" icon={FileText}>
        <p className="leading-6">
          {finalizing
            ? "Supervision is closing. The report is being written from the recorded facts and receipts; it will appear here once it is saved."
            : isClosed(snapshot.status)
              ? "This run is closed but no final report was recorded. Nothing is being inferred to fill the gap."
              : "A final report is written when supervision ends — on delivery, on operator termination, or at the maximum supervision age."}
        </p>
        {snapshot.facts.open_issues.length > 0 ? (
          <div className="mt-4 border-t pt-4">
            <h3 className="text-[13px] font-medium text-muted-foreground">
              Still unresolved right now
            </h3>
            <ul className="mt-2 space-y-2">
              {snapshot.facts.open_issues.map((issue) => (
                <li key={issue.issue_id} className="leading-6">
                  <span className="font-medium">{issue.issue_id}</span> ·{" "}
                  {issue.description}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </Panel>
    );
  }

  async function copy() {
    if (!report) return;
    await navigator.clipboard.writeText(asText(snapshot, report));
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2500);
  }

  return (
    <div className="space-y-5">
      <Panel
        title="Final report"
        icon={FileText}
        action={
          <Button variant="outline" size="sm" onClick={copy}>
            {copied ? (
              <Check className="size-4" aria-hidden="true" />
            ) : (
              <ClipboardCopy className="size-4" aria-hidden="true" />
            )}
            {copied ? "Copied" : "Copy as text"}
          </Button>
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <StateBadge
            label={CLOSE_REASON[report.close_reason]}
            tone={report.close_reason === "delivered" ? "done" : "stopped"}
          />
          <span className="text-[13px] text-muted-foreground">
            Closed{" "}
            <time dateTime={report.closed_at}>
              {new Date(report.closed_at).toLocaleString()}
            </time>
          </span>
        </div>
        <p className="mt-4 leading-6 whitespace-pre-wrap">{report.summary}</p>
        <p className="mt-4 border-t pt-3 text-[13px] leading-5 text-muted-foreground">
          {report.narrative_provenance === "model_assisted"
            ? "The agent wrote the closing text from the recorded facts. The facts, receipts, and unresolved list are not its to change."
            : "The narrative was rendered from the record rather than written by the agent."}{" "}
          {report.narrative_limitation ?? ""} Evidence considered through record #
          {report.evidence_through_sequence}.
        </p>
      </Panel>

      <Panel title="Actions taken">
        {report.important_actions.length === 0 ? (
          <p className="text-muted-foreground">
            No action was recorded for this order.
          </p>
        ) : (
          <ul className="space-y-3.5">
            {report.important_actions.map((action) => (
              <li key={action.action_id}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {ACTION_LABEL[action.action as ActionName] ?? action.action}
                  </span>
                  <StateBadge
                    label="Simulated"
                    tone="stopped"
                    dot={false}
                    className="px-2 py-0.5 text-[12px]"
                  />
                  <span className="text-[13px] text-muted-foreground tabular-nums">
                    receipt #{action.receipt.sequence}
                  </span>
                </div>
                <p className="mt-1 leading-6 text-muted-foreground">
                  {action.content}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {/* Kept apart from work that happened. A refusal is a decision with a reason, not
          a failure to mention. */}
      <Panel title="Proposals that were refused">
        {report.blocked_actions.length === 0 ? (
          <p className="text-muted-foreground">
            Every proposal this run made was carried out.
          </p>
        ) : (
          <ul className="space-y-3.5">
            {report.blocked_actions.map((item) => (
              <li key={`${item.sequence}-${item.action}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">
                    {ACTION_LABEL[item.action as ActionName] ?? item.action}
                  </span>
                  <StateBadge
                    label={BLOCK_REASON[item.reason] ?? item.reason}
                    tone="hold"
                    dot={false}
                    className="px-2 py-0.5 text-[12px]"
                  />
                  <span className="text-[13px] text-muted-foreground tabular-nums">
                    entry #{item.sequence}
                  </span>
                </div>
                <p className="mt-1 leading-6 text-muted-foreground">
                  {item.explanation} Nothing was recorded for it.
                </p>
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Panel title="Unresolved concerns">
        {report.unresolved_issues.length === 0 ? (
          <p className="text-muted-foreground">
            Nothing was left outstanding when supervision closed.
          </p>
        ) : (
          <ul className="space-y-2.5">
            {report.unresolved_issues.map((issue) => (
              <li key={issue.issue_id} className="leading-6">
                <span className="font-medium">{issue.issue_id}</span> ·{" "}
                {issue.description}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Learnings">
          <Bullets
            items={report.learnings}
            empty="Nothing about this order supported a general learning."
          />
        </Panel>
        <Panel title="Feedback and recommendations">
          <Bullets
            items={report.feedback}
            empty="No recommendation was recorded."
          />
        </Panel>
      </div>
    </div>
  );
}
