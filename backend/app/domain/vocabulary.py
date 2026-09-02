from enum import StrEnum
from uuid import UUID

PAYLOAD_BYTES = 8192
# Audit details must hold accepted commands and decision batches with their metadata.
ACTIVITY_DETAILS_BYTES = 128 * 1024
MESSAGE_CHARS = 2000
INSTRUCTION_CHARS = 4000
SUMMARY_CHARS = 1500
PENDING_COMMANDS = 128
RECENT_RECORDS = 12
EVIDENCE_REFERENCES = 128
ACTION_BATCH = 5
STANDARD_WAKE = (30, 300, 3600)
DEMO_WAKE = (10, 20, 60)
DEMO_MAXIMUM_AGE_SECONDS = 1800
PROVIDER_TIMEOUT_SECONDS = 30
PROVIDER_ATTEMPTS = 2


class ActionName(StrEnum):
    MESSAGE_FULFILLMENT_TEAM = "message_fulfillment_team"
    MESSAGE_PAYMENTS_TEAM = "message_payments_team"
    MESSAGE_LOGISTICS_TEAM = "message_logistics_team"
    MESSAGE_CUSTOMER = "message_customer"
    CREATE_INTERNAL_NOTE = "create_internal_note"


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
