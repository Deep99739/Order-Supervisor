/**
 * Turning one recorded row into the line an operator reads.
 *
 * The title says what the record *is*; the disposition badge says what happened to it.
 * Neither is inferred: both come from fields the backend wrote. A proposal that was
 * blocked never gets a title that reads like an action that occurred.
 */
import {
  ACTION_LABEL,
  CLOSE_REASON,
  eventLabel,
  KIND_LABEL,
  type Tone,
} from "./display";
import type {
  ActionName,
  ActivityRecord,
  CloseReason,
  JsonObject,
} from "./contracts";

function text(details: JsonObject, key: string): string | null {
  const value = details[key];
  return typeof value === "string" ? value : null;
}

function count(details: JsonObject, key: string): number | null {
  const value = details[key];
  return typeof value === "number" ? value : null;
}

export function recordTitle(record: ActivityRecord): string {
  const details = record.details;
  switch (record.kind) {
    case "run_reserved":
      return "Run created";
    case "event":
      return eventLabel(text(details, "event_type") ?? "unknown");
    case "policy":
      return "Wake policy";
    case "decision": {
      const stage = text(details, "stage");
      if (stage === "started") return "Reviewing the order";
      if (stage === "discarded") return "Review discarded";
      return "Agent review";
    }
    case "action": {
      const action = text(details, "action");
      return action && action in ACTION_LABEL
        ? ACTION_LABEL[action as ActionName]
        : "Action";
    }
    case "instruction": {
      const operation = text(details, "operation");
      if (operation === "remove") return "Instruction removed";
      if (operation === "supersede") return "Instruction replaced";
      return "Instruction added";
    }
    case "control": {
      const kind = text(details, "kind");
      return kind ? `Operator ${kind}` : "Operator control";
    }
    case "review":
      return "Customer draft review";
    case "sleep":
      return "Next review scheduled";
    case "memory": {
      const version = count(details, "summary_version");
      return version === null ? "Memory summary" : `Memory summary v${version}`;
    }
    case "continuation": {
      if (text(details, "stage") === "prepared") {
        return "Preparing to continue the history";
      }
      const generation = count(details, "execution_generation");
      return generation === null
        ? "History continued"
        : `History continued · generation ${generation}`;
    }
    case "recovery":
      return "Supervision needs attention";
    case "finalization": {
      const reason = text(details, "close_reason");
      return reason && reason in CLOSE_REASON
        ? `Closed · ${CLOSE_REASON[reason as CloseReason]}`
        : "Closing";
    }
    default:
      return KIND_LABEL[record.kind];
  }
}

/**
 * Consecutive rows written by the same decision, kept in sequence order. Grouping is
 * strictly by adjacency: an event that arrived while the model was thinking is recorded
 * between the two halves of that review, and it stays where it happened.
 */
export type FeedGroup = {
  key: string;
  decisionId: string | null;
  records: ActivityRecord[];
};

export function groupRecords(records: ActivityRecord[]): FeedGroup[] {
  const groups: FeedGroup[] = [];
  for (const record of records) {
    const last = groups.at(-1);
    if (
      last &&
      record.decision_id !== null &&
      last.decisionId === record.decision_id
    ) {
      last.records.push(record);
      continue;
    }
    groups.push({
      key: record.id,
      decisionId: record.decision_id,
      records: [record],
    });
  }
  return groups;
}

/**
 * The colour of a row's left edge. Disposition is read before kind on purpose: a refused
 * proposal is a refusal first and an action second, and colouring it like a committed
 * action would be the one mistake this console must never make.
 */
export function accentOf(record: ActivityRecord): Tone {
  if (record.kind === "recovery" || record.disposition === "failed") return "alert";
  if (
    record.disposition === "blocked" ||
    record.disposition === "pending_review" ||
    record.disposition === "review_required" ||
    record.disposition === "rejected" ||
    record.disposition === "conflict" ||
    record.disposition === "too_late" ||
    record.disposition === "capacity_exceeded"
  ) {
    return "hold";
  }
  switch (record.kind) {
    case "event":
    case "run_reserved":
      return "working";
    case "decision":
    case "policy":
    case "review":
      return "quiet";
    case "action":
      return record.disposition === "committed" ? "done" : "quiet";
    case "finalization":
      return "done";
    default:
      return "stopped";
  }
}

/** Which feed filter a record belongs to. Mirrors the API's own category mapping. */
export function categoryOf(
  record: ActivityRecord,
): "events" | "actions" | "system" {
  if (record.kind === "event" || record.kind === "policy") return "events";
  if (
    record.kind === "decision" ||
    record.kind === "action" ||
    record.kind === "review"
  ) {
    return "actions";
  }
  return "system";
}
