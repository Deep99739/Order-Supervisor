from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.commands import PolicyChanges
from app.contracts.common import (
    ActivityDetails,
    Count,
    Digest,
    JsonObject,
    Message,
    PositiveInt,
    Reference,
    ShortText,
    UTCDateTime,
    WireModel,
)
from app.contracts.supervisor import SupervisorConfig
from app.domain.vocabulary import (
    ACTION_LEDGER,
    CLOSED_STATUS,
    EVIDENCE_REFERENCES,
    INSTRUCTION_CHARS,
    RECENT_RECORDS,
    SUMMARY_CHARS,
    ActionAudience,
    ActionName,
    CloseReason,
    ControlKind,
    RunStatus,
    workflow_id,
)


class EvidenceReference(WireModel):
    sequence: PositiveInt
    activity_id: UUID


class IssueContact(WireModel):
    """One committed contact about one issue. Deciding whether to write again needs the
    audience, what was known at the time, and when a follow-up becomes fair."""

    audience: ActionAudience
    action_id: Reference
    context_version: Count
    contacted_at: UTCDateTime
    follow_up_at: UTCDateTime


class OpenIssue(WireModel):
    issue_id: Reference
    description: ShortText
    evidence: Annotated[list[EvidenceReference], Field(min_length=1, max_length=12)]
    review_required: bool = False
    # `contacts` is the authority for repeated-contact checks; the two fields below
    # summarise the most recent contact for display.
    contacts: Annotated[list[IssueContact], Field(max_length=5)] = Field(default_factory=list)
    last_action_id: Reference | None = None
    follow_up_at: UTCDateTime | None = None


class OrderFacts(WireModel):
    payment: Literal["unknown", "pending", "confirmed", "failed"] = "unknown"
    payment_attempt_reference: Reference | None = None
    payment_observed_at: UTCDateTime | None = None
    shipment: Literal["unknown", "not_created", "in_transit", "delayed", "delivered"] = "unknown"
    shipment_reference: Reference | None = None
    shipment_observed_at: UTCDateTime | None = None
    expected_at: UTCDateTime | None = None
    delivered_at: UTCDateTime | None = None
    delivery_evidence_reference: Reference | None = None
    last_relevant_progress_at: UTCDateTime | None = None
    open_issues: Annotated[list[OpenIssue], Field(max_length=EVIDENCE_REFERENCES)] = Field(
        default_factory=list
    )


class ActiveInstruction(WireModel):
    instruction_id: UUID
    text: Annotated[str, StringConstraints(min_length=1, max_length=INSTRUCTION_CHARS)]
    added_at: UTCDateTime
    source_command_id: UUID
    policy_changes: PolicyChanges | None = None


class MemorySummary(WireModel):
    text: Annotated[str, StringConstraints(max_length=SUMMARY_CHARS)] = ""
    summary_version: Count = 0
    summary_through_sequence: Count = 0
    recorded_at: UTCDateTime | None = None


class ContextStamp(WireModel):
    context_version: Count
    control_epoch: Count
    evidence_through_sequence: Count


class CustomerDraft(WireModel):
    """At most one current customer draft per run. `outdated` is the state the plan calls
    stale; a consumed draft is not a state but an absence, because approval is spent in
    the same transaction that records the customer effect."""

    draft_id: Reference
    decision_id: Reference
    action_id: Reference
    issue_id: Reference
    content: Message
    content_digest: Digest
    reason: ShortText
    context: ContextStamp
    status: Literal["pending", "approved", "rejected", "outdated"]
    review_command_id: UUID | None = None


class RecoveryDetail(WireModel):
    reason: ShortText
    next_action: Literal[
        "resolve_pending_write", "retry_decision", "retry_finalization", "consolidate_context"
    ]
    operation_id: Reference | None = None


class RunCounters(WireModel):
    unique_events: Count = 0
    duplicate_events: Count = 0
    decisions: Count = 0
    model_attempts: Count = 0
    deferred_events: Count = 0
    committed_actions: Count = 0
    compactions: Count = 0
    continuations: Count = 0


class CommittedAction(WireModel):
    action_id: Reference
    action: ActionName
    content: Message
    receipt: EvidenceReference
    recorded_at: UTCDateTime
    simulated: Literal[True] = True


class FinalOutput(WireModel):
    close_reason: CloseReason
    closed_at: UTCDateTime
    facts: OrderFacts
    summary: Message
    important_actions: Annotated[list[CommittedAction], Field(max_length=128)]
    unresolved_issues: Annotated[list[OpenIssue], Field(max_length=EVIDENCE_REFERENCES)]
    learnings: Annotated[list[ShortText], Field(max_length=10)]
    feedback: Annotated[list[ShortText], Field(max_length=10)]
    narrative_provenance: Literal["model", "factual_fallback"]
    narrative_limitation: ShortText | None = None
    evidence_through_sequence: Count


class RunSnapshot(WireModel):
    run_id: UUID
    order_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    workflow_id: Reference
    temporal_run_id: UUID | None = None
    supervisor: SupervisorConfig
    initial_context: JsonObject
    status: RunStatus
    pending_control: ControlKind | None = None
    close_reason: CloseReason | None = None
    facts: OrderFacts = Field(default_factory=OrderFacts)
    recorded_revision: Count = 0
    context_version: Count = 0
    control_epoch: Count = 0
    last_sequence: Count = 0
    instructions: Annotated[list[ActiveInstruction], Field(max_length=128)] = Field(
        default_factory=list
    )
    memory: MemorySummary = Field(default_factory=MemorySummary)
    recent_evidence: Annotated[list[EvidenceReference], Field(max_length=RECENT_RECORDS)] = Field(
        default_factory=list
    )
    unresolved_evidence: Annotated[
        list[EvidenceReference], Field(max_length=EVIDENCE_REFERENCES)
    ] = Field(default_factory=list)
    deferred_evidence: Annotated[list[EvidenceReference], Field(max_length=EVIDENCE_REFERENCES)] = (
        Field(default_factory=list)
    )
    last_decision_through_sequence: Count = 0
    next_wake_at: UTCDateTime | None = None
    wake_reason: ShortText | None = None
    started_at: UTCDateTime
    maximum_age_at: UTCDateTime
    updated_at: UTCDateTime
    closed_at: UTCDateTime | None = None
    execution_generation: Count = 0
    pending_review: CustomerDraft | None = None
    recovery: RecoveryDetail | None = None
    # The bounded working view of what has actually been done. Every receipt also stays
    # in the activity log, which remains the complete record.
    committed_actions: Annotated[list[CommittedAction], Field(max_length=ACTION_LEDGER)] = Field(
        default_factory=list
    )
    counters: RunCounters = Field(default_factory=RunCounters)
    final_output: FinalOutput | None = None

    @model_validator(mode="after")
    def coherent_snapshot(self):
        if self.workflow_id != workflow_id(self.run_id):
            raise ValueError("workflow_id must derive from the stable run_id")
        if self.maximum_age_at <= self.started_at:
            raise ValueError("maximum age deadline must follow the original start")
        if sum(len(item.text) for item in self.instructions) > INSTRUCTION_CHARS:
            raise ValueError("active instructions exceed the total text capacity")
        if self.status in CLOSED_STATUS.values():
            if not self.final_output or not self.closed_at:
                raise ValueError("closed runs require a recorded final output and close time")
            if CLOSED_STATUS.get(self.close_reason) != self.status:
                raise ValueError("closed status must match the lifecycle reason")
            if (
                self.final_output.close_reason != self.close_reason
                or self.final_output.closed_at != self.closed_at
            ):
                raise ValueError("final output must match recorded closure")
        elif self.final_output is not None or self.closed_at is not None:
            raise ValueError("an open run cannot claim a saved final output")
        if self.status == RunStatus.AWAITING_RECOVERY and self.recovery is None:
            raise ValueError("recovery state requires a specific next action")
        if self.status == RunStatus.SLEEPING and self.next_wake_at is None:
            raise ValueError("sleeping requires an effective wake deadline")
        return self


ActivityKind = Literal[
    "run_reserved",
    "event",
    "policy",
    "decision",
    "action",
    "instruction",
    "control",
    "review",
    "sleep",
    "memory",
    "continuation",
    "recovery",
    "finalization",
    "operation_receipt",
]

ActivityDisposition = Literal[
    "applied",
    "duplicate",
    "conflict",
    "rejected",
    "too_late",
    "capacity_exceeded",
    "deferred",
    "wake_now",
    "review_required",
    "proposed",
    "blocked",
    "pending_review",
    "committed",
    "failed",
    "recorded",
]

# Receipts prove an operation finished; they are not part of the human timeline.
INTERNAL_KINDS: frozenset[str] = frozenset({"operation_receipt"})

# History filters the run view offers. "all" is every kind except the internal receipts.
ACTIVITY_CATEGORIES: dict[str, frozenset[str]] = {
    "events": frozenset({"event", "policy"}),
    "actions": frozenset({"decision", "action", "review"}),
    "system": frozenset(
        {
            "run_reserved",
            "instruction",
            "control",
            "sleep",
            "memory",
            "continuation",
            "recovery",
            "finalization",
        }
    ),
}


class ActivityRecord(WireModel):
    id: UUID
    run_id: UUID
    sequence: PositiveInt
    kind: ActivityKind
    occurred_at: UTCDateTime | None = None
    recorded_at: UTCDateTime
    command_id: UUID | None = None
    event_id: Reference | None = None
    operation_id: Reference | None = None
    decision_id: Reference | None = None
    action_id: Reference | None = None
    disposition: ActivityDisposition
    explanation: ShortText
    details: ActivityDetails = Field(default_factory=dict)
