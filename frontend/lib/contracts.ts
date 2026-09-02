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

export interface OpenIssue {
  issue_id: string;
  description: string;
  evidence: EvidenceReference[];
  review_required: boolean;
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

export interface MemorySummary {
  text: string;
  summary_version: number;
  summary_through_sequence: number;
  recorded_at: UTCDateTime | null;
}

export interface ContextStamp {
  context_version: number;
  control_epoch: number;
  evidence_through_sequence: number;
}

export interface CustomerDraft {
  draft_id: string;
  decision_id: string;
  content: string;
  content_digest: string;
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

export interface ActionProposal {
  action: ActionName;
  content: string;
  issue_id?: string | null;
  rationale: string;
}

export interface MemoryRefresh {
  text: string;
  through_sequence: number;
}
export interface WakeHint {
  kind: "watch_for_progress" | "shorten_review" | "await_response";
  issue_id: string;
  expires_at: UTCDateTime;
  event_type?: KnownEvent | null;
  review_after_seconds?: number | null;
}
export interface WakeGuidance {
  version: number;
  context: ContextStamp;
  hints: WakeHint[];
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
