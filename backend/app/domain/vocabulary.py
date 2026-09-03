from enum import StrEnum
from uuid import UUID

PAYLOAD_BYTES = 8192
# Audit details must hold accepted commands and decision batches with their metadata.
ACTIVITY_DETAILS_BYTES = 128 * 1024
MESSAGE_CHARS = 2000
SUBJECT_CHARS = 120
INSTRUCTION_CHARS = 4000
SUMMARY_CHARS = 1500
PENDING_COMMANDS = 128
RECENT_RECORDS = 12
EVIDENCE_REFERENCES = 128
ACTION_BATCH = 5
# The committed actions a run carries as working context. The complete list of receipts
# always stays in the activity log; this is the bounded view a decision and the closing
# report can rely on.
ACTION_LEDGER = 32
# A second contact to the same audience about unchanged work becomes eligible after this
# many default review intervals, so the pause scales with the template's own timing.
FOLLOW_UP_INTERVALS = 6
# Compaction is triggered, not continuous: a summary is refreshed once this many records
# have accumulated past its cutoff, or once the deterministic narrative outgrows its cap.
# A demo template refreshes sooner so the behaviour is observable in a short run.
COMPACTION_RECORDS = 20
DEMO_COMPACTION_RECORDS = 12
# Older inputs the agent has not considered yet, retrieved by sequence from the log.
DEFERRED_EVIDENCE_LIMIT = 12
# The serialized ceiling for one decision's assembled context. Beyond this the run asks
# an operator to consolidate rather than silently dropping an instruction or a question.
CONTEXT_BUDGET_BYTES = 48 * 1024
# Active wake hints. Three is enough to be useful and small enough to audit.
GUIDANCE_HINTS = 3
# Temporal history events before the execution rolls over. Counted per generation so a
# fresh execution cannot immediately continue again.
CONTINUATION_EVENTS = 2000
DEMO_CONTINUATION_EVENTS = 120
STANDARD_WAKE = (30, 300, 3600)
DEMO_WAKE = (10, 20, 60)
DEMO_MAXIMUM_AGE_SECONDS = 1800
PROVIDER_TIMEOUT_SECONDS = 30
PROVIDER_ATTEMPTS = 2
# A refused request (rate limit, transient outage) may move to the next configured key.
# This bounds that transport rotation; it never buys another reasoning attempt.
PROVIDER_KEYS_PER_ATTEMPT = 3


class ActionName(StrEnum):
    MESSAGE_FULFILLMENT_TEAM = "message_fulfillment_team"
    MESSAGE_PAYMENTS_TEAM = "message_payments_team"
    MESSAGE_LOGISTICS_TEAM = "message_logistics_team"
    MESSAGE_CUSTOMER = "message_customer"
    CREATE_INTERNAL_NOTE = "create_internal_note"


class ActionAudience(StrEnum):
    """Who a recorded action is directed at. Supplied by the registry, never by the model."""

    FULFILLMENT_TEAM = "fulfillment_team"
    PAYMENTS_TEAM = "payments_team"
    LOGISTICS_TEAM = "logistics_team"
    CUSTOMER = "customer"
    INTERNAL = "internal"


class NoteCategory(StrEnum):
    OBSERVATION = "observation"
    ESCALATION = "escalation"
    RECOMMENDATION = "recommendation"


class BlockReason(StrEnum):
    """Why one proposal did not become an effect. These are different operator decisions,
    so they stay distinguishable in history rather than collapsing into "failed"."""

    RUN_CLOSING = "run_closing"
    RUN_HELD = "run_held"
    STALE_CONTEXT = "stale_context"
    NOT_PERMITTED = "not_permitted"
    INVALID_ARGUMENTS = "invalid_arguments"
    UNKNOWN_ISSUE = "unknown_issue"
    REPEATED_CONTACT = "repeated_contact"
    APPROVAL_REQUIRED = "approval_required"
    DRAFT_PENDING = "draft_pending"


class RunStatus(StrEnum):
    STARTING = "starting"
    EVALUATING = "evaluating"
    APPLYING = "applying"
    SLEEPING = "sleeping"
    PAUSED = "paused"
    AWAITING_RECOVERY = "awaiting_recovery"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    TERMINATED = "terminated"
    EXPIRED = "expired"


class CloseReason(StrEnum):
    DELIVERED = "delivered"
    MANUALLY_TERMINATED = "manually_terminated"
    MAXIMUM_AGE_REACHED = "maximum_age_reached"


CLOSED_STATUS = {
    CloseReason.DELIVERED: RunStatus.COMPLETED,
    CloseReason.MANUALLY_TERMINATED: RunStatus.TERMINATED,
    CloseReason.MAXIMUM_AGE_REACHED: RunStatus.EXPIRED,
}


class ControlKind(StrEnum):
    PAUSE = "pause"
    INTERRUPT = "interrupt"
    RESUME = "resume"
    TERMINATE = "terminate"


class DecisionTrigger(StrEnum):
    START = "start"
    IMPORTANT_EVENT = "important_event"
    SCHEDULED_WAKE = "scheduled_wake"
    # Resume, recovery, and new instructions ask for one reassessment. Analytics keep this
    # separate from the three ordinary triggers the assignment names.
    CONTROL_REASSESSMENT = "control_reassessment"


ORDINARY_TRIGGERS = frozenset(
    {DecisionTrigger.START, DecisionTrigger.IMPORTANT_EVENT, DecisionTrigger.SCHEDULED_WAKE}
)


class KnownEvent(StrEnum):
    ORDER_CREATED = "order_created"
    PAYMENT_CONFIRMED = "payment_confirmed"
    PAYMENT_FAILED = "payment_failed"
    SHIPMENT_CREATED = "shipment_created"
    SHIPMENT_DELAYED = "shipment_delayed"
    DELIVERED = "delivered"
    REFUND_REQUESTED = "refund_requested"
    CUSTOMER_MESSAGE_RECEIVED = "customer_message_received"
    NO_UPDATE_FOR_N_HOURS = "no_update_for_n_hours"


# Transport names shared by the API and the worker. The API starts and signals by name so
# it never imports workflow code.
WORKFLOW_TYPE = "OrderSupervisor"
EVENT_SIGNAL = "event"
INSTRUCTION_SIGNAL = "instruction"
CONTROL_SIGNAL = "control"
REVIEW_SIGNAL = "review"


def workflow_id(run_id: UUID) -> str:
    return f"order-supervisor/{run_id}"


def operation_id(run_id: UUID, counter: int) -> str:
    if counter < 1:
        raise ValueError("operation counter starts at 1")
    return f"{run_id}/operation/{counter}"


def decision_id(run_id: UUID, counter: int) -> str:
    if counter < 1:
        raise ValueError("decision counter starts at 1")
    return f"{run_id}/decision/{counter}"


def action_id(decision: str, ordinal: int) -> str:
    if not 1 <= ordinal <= ACTION_BATCH:
        raise ValueError("action ordinal must be within the decision batch")
    return f"{decision}/action/{ordinal}"
