// Explicit mirror of backend/app/contracts. Pydantic remains the validation authority.
export type UUID = string;
export type UTCDateTime = string;
export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };

export type ActionName =
  | "message_fulfillment_team"
  | "message_payments_team"
  | "message_logistics_team"
  | "message_customer"
  | "create_internal_note";

export type RunStatus =
  | "starting"
  | "evaluating"
  | "applying"
  | "sleeping"
  | "paused"
  | "awaiting_recovery"
  | "finalizing"
  | "completed"
  | "terminated"
  | "expired";

export type CloseReason =
  | "delivered"
  | "manually_terminated"
  | "maximum_age_reached";
export type ControlKind = "pause" | "interrupt" | "resume" | "terminate";
export type KnownEvent =
  | "order_created"
  | "payment_confirmed"
  | "payment_failed"
  | "shipment_created"
  | "shipment_delayed"
  | "delivered"
  | "refund_requested"
  | "customer_message_received"
  | "no_update_for_n_hours";

export interface WakeProfile {
  mode: "standard" | "demo";
  minimum_seconds: number;
  default_seconds: number;
  maximum_seconds: number;
}

export interface SupervisorConfig {
  id: UUID;
  version: number;
  name: string;
  base_instructions: string;
  allowed_actions: ActionName[];
  wake_profile: WakeProfile;
  maximum_age_seconds: number;
  customer_review_default: boolean;
  escalate_shipment_delays: boolean;
  prioritize_speed: boolean;
  model_label: string | null;
}

export interface CreateRunRequest {
  command_id: UUID;
  supervisor_id: UUID;
  order_id: string;
  initial_context: JsonObject;
  demo_timing_preset?: "short_review" | "short_expiry" | null;
}

export interface EventCommand {
  command_id: UUID;
  event_id: string;
  event_type: KnownEvent | (string & {});
  occurred_at: UTCDateTime;
  payload: JsonObject;
}

export interface PolicyChanges {
  prioritize_speed?: boolean | null;
  escalate_shipment_delays?: boolean | null;
  require_customer_review?: boolean | null;
}

export interface InstructionCommand {
  command_id: UUID;
  operation: "add" | "supersede" | "remove";
  instruction_id?: UUID | null;
  text?: string | null;
  policy_changes?: PolicyChanges | null;
}

export interface ControlCommand {
  command_id: UUID;
  kind: ControlKind;
  reason?: string | null;
}

export interface ReviewCommand {
  command_id: UUID;
  draft_id: string;
  content_digest: string;
  decision: "approve" | "reject";
}

export interface CommandAcknowledgement {
  command_id: UUID;
  run_id: UUID;
  acceptance: "accepted";
  processing: "pending";
}

export interface EvidenceReference {
  sequence: number;
  activity_id: UUID;
}

// Who a recorded action was directed at. Supplied by the action registry, never by the
// model, so the console can label an audience without trusting generated text.
export type ActionAudience =
  | "fulfillment_team"
  | "payments_team"
  | "logistics_team"
  | "customer"
  | "internal";

export const ACTION_AUDIENCE: Record<ActionName, ActionAudience> = {
  message_fulfillment_team: "fulfillment_team",
  message_payments_team: "payments_team",
  message_logistics_team: "logistics_team",
  message_customer: "customer",
  create_internal_note: "internal",
};

export type NoteCategory = "observation" | "escalation" | "recommendation";

// `evidence_sequence` is how far this issue's own evidence reached when the message went
// out — the test for whether there is anything new to say to the same audience.
export interface IssueContact {
  audience: ActionAudience;
  action_id: string;
  evidence_sequence: number;
  context_version: number;
  contacted_at: UTCDateTime;
  follow_up_at: UTCDateTime;
}

export interface OpenIssue {
  issue_id: string;
  description: string;
  evidence: EvidenceReference[];
  review_required: boolean;
  // `contacts` decides whether another message is justified; the two fields below
  // summarise the most recent one for display.
  contacts: IssueContact[];
  last_action_id: string | null;
  follow_up_at: UTCDateTime | null;
}

export interface OrderFacts {
  payment: "unknown" | "pending" | "confirmed" | "failed";
  payment_attempt_reference: string | null;
  payment_observed_at: UTCDateTime | null;
  shipment: "unknown" | "not_created" | "in_transit" | "delayed" | "delivered";
  shipment_reference: string | null;
  shipment_observed_at: UTCDateTime | null;
  expected_at: UTCDateTime | null;
  delivered_at: UTCDateTime | null;
  delivery_evidence_reference: string | null;
  last_relevant_progress_at: UTCDateTime | null;
  open_issues: OpenIssue[];
}

export interface ActiveInstruction {
  instruction_id: UUID;
  text: string;
  added_at: UTCDateTime;
  source_command_id: UUID;
  policy_changes: PolicyChanges | null;
}

// `summary_through_sequence` is the evidence cutoff: entries after it are outside what
// this text covers, which is what stops it reading as a complete account of the run.
export interface MemorySummary {
  text: string;
  summary_version: number;
  summary_through_sequence: number;
  recorded_at: UTCDateTime | null;
  provenance: "deterministic" | "model";
  source_decision_id: string | null;
}

export interface ContextStamp {
  context_version: number;
  control_epoch: number;
  evidence_through_sequence: number;
}

// At most one current draft per run. "outdated" is the stale state; a consumed draft is
// an absence, because approval is spent in the transaction that records the message.
export interface CustomerDraft {
  draft_id: string;
  decision_id: string;
  action_id: string;
  issue_id: string;
  content: string;
  content_digest: string;
  reason: string;
  context: ContextStamp;
  status: "pending" | "approved" | "rejected" | "outdated";
  review_command_id: UUID | null;
}

export interface RecoveryDetail {
  reason: string;
  next_action:
    | "resolve_pending_write"
    | "retry_decision"
    | "retry_finalization"
    | "consolidate_context";
  operation_id: string | null;
}

export interface RunCounters {
  unique_events: number;
  duplicate_events: number;
  decisions: number;
  model_attempts: number;
  deferred_events: number;
  committed_actions: number;
  compactions: number;
  continuations: number;
}

export interface CommittedAction {
  action_id: string;
  action: ActionName;
  content: string;
  receipt: EvidenceReference;
  recorded_at: UTCDateTime;
  simulated: true;
}

export interface FinalOutput {
  close_reason: CloseReason;
  closed_at: UTCDateTime;
  facts: OrderFacts;
  summary: string;
  important_actions: CommittedAction[];
  unresolved_issues: OpenIssue[];
  learnings: string[];
  feedback: string[];
  narrative_provenance: "model" | "factual_fallback";
  narrative_limitation: string | null;
  evidence_through_sequence: number;
}

export interface RunSnapshot {
  run_id: UUID;
  order_id: string;
  workflow_id: string;
  temporal_run_id: UUID | null;
  supervisor: SupervisorConfig;
  initial_context: JsonObject;
  status: RunStatus;
  pending_control: ControlKind | null;
  close_reason: CloseReason | null;
  facts: OrderFacts;
  recorded_revision: number;
  context_version: number;
  control_epoch: number;
  last_sequence: number;
  instructions: ActiveInstruction[];
  memory: MemorySummary;
  recent_evidence: EvidenceReference[];
  unresolved_evidence: EvidenceReference[];
  deferred_evidence: EvidenceReference[];
  last_decision_through_sequence: number;
  next_wake_at: UTCDateTime | null;
  wake_reason: string | null;
  started_at: UTCDateTime;
  maximum_age_at: UTCDateTime;
  updated_at: UTCDateTime;
  closed_at: UTCDateTime | null;
  execution_generation: number;
  pending_review: CustomerDraft | null;
  recovery: RecoveryDetail | null;
  wake_guidance: WakeGuidance | null;
  // The bounded working view of committed receipts. The activity log stays complete.
  committed_actions: CommittedAction[];
  counters: RunCounters;
  final_output: FinalOutput | null;
}

export type ActivityKind =
  | "run_reserved"
  | "event"
  | "policy"
  | "decision"
  | "action"
  | "instruction"
  | "control"
  | "review"
  | "sleep"
  | "memory"
  | "continuation"
  | "recovery"
  | "finalization"
  | "operation_receipt";

export type ActivityDisposition =
  | "applied"
  | "duplicate"
  | "conflict"
  | "rejected"
  | "too_late"
  | "capacity_exceeded"
  | "deferred"
  | "wake_now"
  | "review_required"
  | "proposed"
  | "blocked"
  | "pending_review"
  | "committed"
  | "failed"
  | "recorded";

export interface ActivityRecord {
  id: UUID;
  run_id: UUID;
  sequence: number;
  kind: ActivityKind;
  occurred_at: UTCDateTime | null;
  recorded_at: UTCDateTime;
  command_id: UUID | null;
  event_id: string | null;
  operation_id: string | null;
  decision_id: string | null;
  action_id: string | null;
  disposition: ActivityDisposition;
  explanation: string;
  details: JsonObject;
}

// What the agent asks for. Which fields a given action actually requires is enforced by
// the backend registry, so this stays the transport shape.
export interface ActionProposal {
  action: ActionName;
  content: string;
  subject?: string | null;
  category?: NoteCategory | null;
  issue_id?: string | null;
  rationale: string;
}

export interface MemoryRefresh {
  text: string;
  through_sequence: number;
}
// A finite hint vocabulary, not generated rules: the classifier stays inspectable.
// A hint can bring a review forward or let routine progress pass; it can never grant
// permission, silence a terminal event, or override an operator restriction.
export interface WakeHint {
  kind: "watch_for_progress" | "shorten_review" | "defer_routine";
  expires_at: UTCDateTime;
  issue_id: string | null;
  event_type: KnownEvent | null;
  review_after_seconds: number | null;
}

export interface WakeGuidance {
  version: number;
  context: ContextStamp;
  hints: WakeHint[];
  source_decision_id: string | null;
}

export interface DecisionProposal {
  rationale: string;
  actions?: ActionProposal[];
  sleep_for_seconds?: number | null;
  sleep_until?: UTCDateTime | null;
  memory_refresh?: MemoryRefresh | null;
  wake_guidance?: WakeGuidance | null;
  completion_recommendation?: string | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  retryable: boolean;
  field_details?: Record<string, string>;
  command_id?: UUID | null;
  run_id?: UUID | null;
}

// Supervisor configuration. Identity and version are assigned by the API; a run keeps
// the frozen copy it snapshotted, so saving an edit never changes a run in progress.
export interface SupervisorDraft {
  name: string;
  base_instructions: string;
  allowed_actions: ActionName[];
  wake_profile?: WakeProfile;
  maximum_age_seconds?: number | null;
  customer_review_default?: boolean;
  escalate_shipment_delays?: boolean;
  prioritize_speed?: boolean;
  model_label?: string | null;
}

export interface SupervisorUpdate extends SupervisorDraft {
  expected_version: number;
}

export interface SupervisorRecord {
  config: SupervisorConfig;
  is_preset: boolean;
  created_at: UTCDateTime;
  updated_at: UTCDateTime;
}

export interface SupervisorList {
  supervisors: SupervisorRecord[];
}

// The reservation is settled either way; `retry_required` means the workflow start was
// not confirmed and the same request should be sent again with the same command_id.
export type StartState = "started" | "retry_required";

export interface RunCreated {
  command_id: UUID;
  run_id: UUID;
  order_id: string;
  workflow_id: string;
  status: RunStatus;
  start: StartState;
  start_detail: string | null;
}

export interface RunListItem {
  run_id: UUID;
  order_id: string;
  supervisor_name: string;
  initial_context: JsonObject;
  status: RunStatus;
  pending_control: ControlKind | null;
  close_reason: CloseReason | null;
  facts: OrderFacts;
  next_wake_at: UTCDateTime | null;
  updated_at: UTCDateTime;
  closed_at: UTCDateTime | null;
}

export interface RunPage {
  runs: RunListItem[];
  next_cursor: string | null;
  observed_at: UTCDateTime;
}

export interface RunView {
  snapshot: RunSnapshot;
  observed_at: UTCDateTime;
}

export type ActivityCategory = "all" | "events" | "actions" | "system";

// Read from an activity record's `details`. The three ordinary triggers are the ones the
// assignment names; a control reassessment is deliberately not one of them.
export type DecisionTrigger =
  | "start"
  | "important_event"
  | "scheduled_wake"
  | "control_reassessment";

// The wake policy's verdict, which is also the disposition its record carries.
// "deferred" means recorded without inference now — never discarded.
export type PolicyOutcome = "wake_now" | "deferred" | "review_required";

// A scripted stand-in decision must never be shown as a model decision.
export type DecisionProvenance = "scripted" | "model";

// Read from a blocked action record's `details`. These stay distinct because they are
// different operator decisions: "not allowed" is not "already asked them".
export type BlockReason =
  | "run_closing"
  | "run_held"
  | "stale_context"
  | "not_permitted"
  | "invalid_arguments"
  | "unknown_issue"
  | "repeated_contact"
  | "approval_required"
  | "draft_pending";

// Records ascend by sequence. `through_sequence` is the bound that was applied, so a
// newer receipt is never merged into an older view of the order's facts.
export interface ActivityPage {
  records: ActivityRecord[];
  earlier_cursor: number | null;
  through_sequence: number;
  last_sequence: number;
  observed_at: UTCDateTime;
}
