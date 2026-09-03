"use client";

import { Check, ShieldCheck, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { StateBadge } from "@/components/state-badge";
import { useNotify } from "@/components/ui/notification";
import { reviewDraft } from "@/lib/api";
import { useCommand } from "@/lib/use-command";
import type { RunSnapshot } from "@/lib/contracts";

/**
 * One exact draft, approved or rejected as it stands.
 *
 * The text is never edited here and never optimistically shown as sent. Approval is
 * spent in the transaction that records the message, so the customer receipt appears in
 * the timeline as its own entry — that receipt, not this card, is the evidence.
 */
export function DraftReviewCard({
  runId,
  snapshot,
  onSubmitted,
}: {
  runId: string;
  snapshot: RunSnapshot;
  onSubmitted: () => void;
}) {
  const notify = useNotify();
  const command = useCommand();
  const draft = snapshot.pending_review;

  if (!draft) return null;

  const outdated =
    draft.status === "outdated" ||
    draft.context.context_version !== snapshot.context_version;
  const paused = snapshot.status === "paused";
  const decided = draft.status === "approved" || draft.status === "rejected";
  const busy = command.status === "sending";

  async function decide(decision: "approve" | "reject") {
    if (!draft) return;
    const sent = await command.send((commandId) =>
      reviewDraft(runId, {
        command_id: commandId,
        draft_id: draft.draft_id,
        content_digest: draft.content_digest,
        decision,
      }),
    );
    command.restart();
    if (sent) {
      onSubmitted();
      notify({
        tone: "info",
        title:
          decision === "approve" ? "Approval accepted" : "Rejection accepted",
        detail:
          decision === "approve"
            ? "The message is recorded when the run applies it, and appears as its own receipt."
            : "No customer message will be recorded for this draft.",
      });
    } else {
      notify({
        tone: "problem",
        title: "That review was not accepted",
        detail: command.error?.message ?? "Try again.",
      });
    }
  }

  return (
    <section className="panel overflow-hidden border-hold/30">
      <div className="flex items-center justify-between gap-3 border-b border-hold/20 bg-hold-surface px-4 py-3">
        <h2 className="flex items-center gap-2 font-medium text-hold">
          <ShieldCheck className="size-4" aria-hidden="true" />
          Customer message needs approval
        </h2>
        <StateBadge
          label={
            outdated
              ? "Outdated"
              : decided
                ? draft.status === "approved"
                  ? "Approved"
                  : "Rejected"
                : "Waiting"
          }
          tone={outdated ? "stopped" : decided ? "quiet" : "hold"}
          dot={false}
          className="px-2 py-0.5 text-[12px]"
        />
      </div>
      <div className="px-4 py-4">
        <p className="text-[13px] leading-5 text-muted-foreground">
          {draft.reason}
        </p>
        <p className="mt-3 rounded-lg border-l-2 border-hold/50 bg-muted/60 px-3 py-2 leading-6 whitespace-pre-wrap">
          {draft.content}
        </p>
        <p className="mt-2 text-[13px] text-muted-foreground">
          Prepared by review {draft.decision_id.split("/").at(-1)} · about “
          {draft.issue_id}”
        </p>

        {outdated ? (
          <p className="mt-4 rounded-lg bg-stopped-surface p-3 text-[13px] leading-5 text-stopped">
            Draft outdated — the order&rsquo;s context changed after this was
            written, so it can no longer be approved as it stands. The agent will
            write a new one if the customer still needs an update. Look at the
            entries after record #{draft.context.evidence_through_sequence} to see
            what changed.
          </p>
        ) : decided ? (
          <p className="mt-4 text-[13px] leading-5 text-muted-foreground">
            This review is recorded. Any resulting customer message appears in the
            timeline as its own receipt.
          </p>
        ) : (
          <>
            {paused ? (
              <p className="mt-4 rounded-lg bg-hold-surface p-3 text-[13px] leading-5 text-hold">
                The run is paused. An approval is recorded now, but no message is
                recorded until the run resumes.
              </p>
            ) : null}
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                className="h-10"
                onClick={() => decide("approve")}
                disabled={busy}
              >
                <Check className="size-4" aria-hidden="true" />
                Approve as written
              </Button>
              <Button
                variant="outline"
                className="h-10"
                onClick={() => decide("reject")}
                disabled={busy}
              >
                <X className="size-4" aria-hidden="true" />
                Reject
              </Button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
