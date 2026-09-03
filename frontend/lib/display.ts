/**
 * The words this console uses for recorded state.
 *
 * Everything here is a pure function of what the API returned. Nothing in this file
 * infers an effect, softens a refusal, or fills a gap with a friendlier word: a blocked
 * action says it was blocked, a manually stopped run is not coloured as a failure, and a
 * run that is still pausing is not called paused.
 */
import type {
  ActionAudience,
  ActionName,
  ActivityDisposition,
  ActivityKind,
  BlockReason,
  CloseReason,
  DecisionTrigger,
  NoteCategory,
  OrderFacts,
  RunSnapshot,
  RunStatus,
  WakeHint,
} from "./contracts";

export type Tone = "working" | "quiet" | "hold" | "done" | "stopped" | "alert";

export const TONE_CLASS: Record<Tone, string> = {
  working: "bg-working-surface text-working",
  quiet: "bg-quiet-surface text-quiet",
  hold: "bg-hold-surface text-hold",
  done: "bg-done-surface text-done",
  stopped: "bg-stopped-surface text-stopped",
  alert: "bg-alert-surface text-alert",
};

export const TONE_DOT: Record<Tone, string> = {
  working: "bg-working",
  quiet: "bg-quiet",
  hold: "bg-hold",
  done: "bg-done",
  stopped: "bg-stopped",
  alert: "bg-alert",
};

export type StateLabel = { label: string; tone: Tone; hint: string };

const RUN_STATE: Record<RunStatus, StateLabel> = {
  starting: {
    label: "Starting",
    tone: "working",
    hint: "The order is reserved and its supervisor is being started.",
  },
  evaluating: {
    label: "Reviewing",
    tone: "working",
    hint: "The agent is reviewing this order now.",
  },
  applying: {
    label: "Recording",
    tone: "working",
    hint: "A decision is being written to the record.",
  },
  sleeping: {
    label: "Sleeping",
    tone: "quiet",
    hint: "Waiting for an event or the next scheduled review.",
  },
  paused: {
    label: "Paused",
    tone: "hold",
    hint: "Events are recorded; agent actions are paused.",
  },
  awaiting_recovery: {
    label: "Needs attention",
    tone: "alert",
    hint: "Supervision stopped on an operational problem and is waiting for an operator.",
  },
  finalizing: {
    label: "Finalizing",
    tone: "working",
    hint: "The closing report is being written.",
  },
  completed: {
    label: "Delivered",
    tone: "done",
    hint: "Supervision closed because delivery was recorded.",
  },
  terminated: {
    label: "Terminated",
    tone: "stopped",
    hint: "Supervision was ended by an operator.",
  },
  expired: {
    label: "Aged out",
    tone: "stopped",
    hint: "Supervision reached the maximum age set when the run started.",
  },
};

/**
 * A pause is not applied the moment the button is pressed. Until the workflow's own
 * receipt arrives the run is *pausing*, which is the only honest label for a state where
 * an already-started action may still be finishing.
 */
export function supervisorState(run: {
  status: RunStatus;
  pending_control: RunSnapshot["pending_control"];
}): StateLabel {
  if (run.pending_control === "pause" || run.pending_control === "interrupt") {
    if (run.status !== "paused") {
      return {
        label: "Pausing",
        tone: "hold",
        hint: "An already-started action is finishing. The run is not paused yet.",
      };
    }
  }
  if (run.pending_control === "resume" && run.status === "paused") {
    return {
      label: "Resuming",
      tone: "hold",
      hint: "The resume request was accepted and has not been applied yet.",
    };
  }
  if (run.pending_control === "terminate" && !CLOSED_STATUSES.has(run.status)) {
    return {
      label: "Terminating",
      tone: "working",
      hint: "Termination was accepted. The closing report has not been recorded yet.",
    };
  }
  return RUN_STATE[run.status];
}

export const CLOSED_STATUSES = new Set<RunStatus>([
  "completed",
  "terminated",
  "expired",
]);

export function isClosed(status: RunStatus): boolean {
  return CLOSED_STATUSES.has(status);
}

const PAYMENT: Record<OrderFacts["payment"], string> = {
  unknown: "Payment unknown",
  pending: "Payment pending",
  confirmed: "Paid",
  failed: "Payment failed",
};

const SHIPMENT: Record<OrderFacts["shipment"], string> = {
  unknown: "no shipment yet",
  not_created: "no shipment yet",
  in_transit: "in transit",
  delayed: "shipment delayed",
  delivered: "delivered",
};

/** What the order itself is doing, as distinct from what its supervisor is doing. */
export function progressSummary(facts: OrderFacts): string {
  return `${PAYMENT[facts.payment]} · ${SHIPMENT[facts.shipment]}`;
}

export function progressTone(facts: OrderFacts): Tone {
  if (facts.shipment === "delivered") return "done";
  if (facts.payment === "failed" || facts.shipment === "delayed") return "hold";
  if (facts.open_issues.length > 0) return "hold";
  return "quiet";
}

export const CLOSE_REASON: Record<CloseReason, string> = {
  delivered: "Delivered",
  manually_terminated: "Terminated by an operator",
  maximum_age_reached: "Reached its maximum supervision age",
};

export const EVENT_LABEL: Record<string, string> = {
  order_created: "Order created",
  payment_confirmed: "Payment confirmed",
  payment_failed: "Payment failed",
  shipment_created: "Shipment created",
  shipment_delayed: "Shipment delayed",
  delivered: "Delivered",
  refund_requested: "Refund requested",
  customer_message_received: "Customer message",
  no_update_for_n_hours: "No update reported",
};

export function eventLabel(eventType: string): string {
  return EVENT_LABEL[eventType] ?? `Unfamiliar event · ${eventType}`;
}

export const ACTION_LABEL: Record<ActionName, string> = {
  message_fulfillment_team: "Message to fulfillment",
  message_payments_team: "Message to payments",
  message_logistics_team: "Message to logistics",
  message_customer: "Message to the customer",
  create_internal_note: "Internal note",
};

export const AUDIENCE_LABEL: Record<ActionAudience, string> = {
  fulfillment_team: "Fulfillment team",
  payments_team: "Payments team",
  logistics_team: "Logistics team",
  customer: "Customer",
  internal: "Internal",
};

export const NOTE_CATEGORY_LABEL: Record<NoteCategory, string> = {
  observation: "Observation",
  escalation: "Escalation",
  recommendation: "Recommendation",
};

/** Why a proposal did not become an effect. Each of these is a different decision. */
export const BLOCK_REASON: Record<BlockReason, string> = {
  run_closing: "The run was closing",
  run_held: "The run was paused",
  stale_context: "The order changed during the review",
  not_permitted: "Not allowed by this supervisor",
  invalid_arguments: "The arguments were unusable",
  unknown_issue: "It named a concern that is not open",
  repeated_contact: "The same audience was already contacted about this",
  approval_required: "Customer contact needs approval",
  draft_pending: "A customer draft is already waiting",
};

export const TRIGGER_LABEL: Record<DecisionTrigger, string> = {
  start: "Supervision started",
  important_event: "An important event arrived",
  scheduled_wake: "A scheduled review came due",
  control_reassessment: "An operator change asked for a reassessment",
};

export const HINT_LABEL: Record<WakeHint["kind"], string> = {
  watch_for_progress: "Wake on a specific event",
  shorten_review: "Review again sooner",
  defer_routine: "Let routine progress pass",
};

export const RECOVERY_ACTION: Record<
  NonNullable<RunSnapshot["recovery"]>["next_action"],
  string
> = {
  resolve_pending_write: "Resolve pending write",
  retry_decision: "Retry the review",
  retry_finalization: "Retry finalization",
  consolidate_context: "Consolidate the context",
};

export const KIND_LABEL: Record<ActivityKind, string> = {
  run_reserved: "Run created",
  event: "Order event",
  policy: "Wake policy",
  decision: "Agent review",
  action: "Action",
  instruction: "Instruction",
  control: "Operator control",
  review: "Customer review",
  sleep: "Next review",
  memory: "Memory",
  continuation: "History continued",
  recovery: "Recovery",
  finalization: "Closure",
  operation_receipt: "Write receipt",
};

/**
 * The disposition is the outcome the backend recorded, and the tone follows it exactly.
 * `proposed` and `blocked` are never coloured as if something happened.
 */
export const DISPOSITION_LABEL: Record<ActivityDisposition, string> = {
  applied: "Applied",
  duplicate: "Repeat delivery",
  conflict: "Conflicting record",
  rejected: "Rejected",
  too_late: "Too late",
  capacity_exceeded: "Capacity reached",
  deferred: "Deferred",
  wake_now: "Reviewed now",
  review_required: "Needs review",
  proposed: "Proposed",
  blocked: "Blocked",
  pending_review: "Waiting for approval",
  committed: "Recorded",
  failed: "Failed",
  recorded: "Recorded",
};

export const DISPOSITION_TONE: Record<ActivityDisposition, Tone> = {
  applied: "quiet",
  duplicate: "stopped",
  conflict: "alert",
  rejected: "alert",
  too_late: "stopped",
  capacity_exceeded: "alert",
  deferred: "stopped",
  wake_now: "working",
  review_required: "hold",
  proposed: "stopped",
  blocked: "hold",
  pending_review: "hold",
  committed: "done",
  failed: "alert",
  recorded: "quiet",
};

// --------------------------------------------------------------------- time

export function absoluteTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  });
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function relativeTime(iso: string, now: number): string {
  const seconds = Math.round((now - Date.parse(iso)) / 1000);
  if (!Number.isFinite(seconds)) return "unknown";
  if (seconds < 0) return "just now";
  if (seconds < 45) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

/** `mm:ss` for anything under an hour, `h:mm:ss` above it. Never negative. */
export function countdown(targetIso: string, now: number): string {
  const remaining = Math.max(0, Math.round((Date.parse(targetIso) - now) / 1000));
  const seconds = remaining % 60;
  const minutes = Math.floor(remaining / 60) % 60;
  const hours = Math.floor(remaining / 3600);
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`;
}

export function durationLabel(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 86400) {
    const hours = seconds / 3600;
    return `${Number.isInteger(hours) ? hours : hours.toFixed(1)} h`;
  }
  const days = seconds / 86400;
  return `${Number.isInteger(days) ? days : days.toFixed(1)} d`;
}

/** A short, readable line from whatever synthetic context the operator supplied. */
export function contextLine(context: Record<string, unknown>): string | null {
  const parts: string[] = [];
  const description = context.description;
  const customer = context.customer_display_name;
  if (typeof description === "string" && description.trim()) parts.push(description);
  if (typeof customer === "string" && customer.trim()) parts.push(`for ${customer}`);
  return parts.length > 0 ? parts.join(" ") : null;
}
