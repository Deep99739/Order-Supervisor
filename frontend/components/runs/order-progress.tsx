import { AlertTriangle, Check, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { orderProgress, progressSummary, type ProgressStep } from "@/lib/display";
import type { OrderFacts } from "@/lib/contracts";

const PIP: Record<ProgressStep["state"], string> = {
  reached: "border-done bg-done text-white",
  failed: "border-hold bg-hold text-white",
  at_risk: "border-hold bg-hold-surface text-hold",
  awaited: "border-working bg-working-surface text-working",
  pending: "border-input bg-card text-transparent",
};

const LABEL: Record<ProgressStep["state"], string> = {
  reached: "font-medium text-foreground",
  failed: "font-medium text-hold",
  at_risk: "font-medium text-hold",
  awaited: "font-medium text-working",
  pending: "text-muted-foreground",
};

/**
 * The order's four milestones as a row rather than a sentence. Every pip is still
 * labelled in words and carries its own reason as a tooltip, so the shapes are a summary
 * of the recorded facts and never the only statement of them.
 */
export function OrderProgress({
  facts,
  closed = false,
}: {
  facts: OrderFacts;
  closed?: boolean;
}) {
  const steps = orderProgress(facts, { closed });
  return (
    <ol
      className="flex min-w-0 items-center gap-1"
      aria-label={`Order progress: ${progressSummary(facts)}`}
    >
      {steps.map((step, index) => (
        <li key={step.label} className="flex min-w-0 items-center gap-1">
          {index > 0 ? (
            // The connector belongs to the step behind it: how far the order actually
            // got, not what is expected next.
            <span
              aria-hidden="true"
              className={cn(
                "mr-1 h-px w-4 shrink-0 sm:w-6",
                steps[index - 1].state === "reached" ? "bg-done/45" : "bg-border",
              )}
            />
          ) : null}
          <span className="flex items-center gap-1.5" title={step.detail}>
            <span
              aria-hidden="true"
              className={cn(
                "flex size-4 shrink-0 items-center justify-center rounded-full border-[1.5px]",
                PIP[step.state],
              )}
            >
              {step.state === "reached" ? (
                <Check className="size-2.5" strokeWidth={3.5} />
              ) : step.state === "failed" ? (
                <X className="size-2.5" strokeWidth={3.5} />
              ) : step.state === "at_risk" ? (
                <AlertTriangle className="size-2.5" strokeWidth={3} />
              ) : null}
            </span>
            <span className={cn("text-[13px] whitespace-nowrap", LABEL[step.state])}>
              {step.label}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
