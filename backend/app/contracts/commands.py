from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    Digest,
    JsonObject,
    Message,
    Reference,
    ShortText,
    UTCDateTime,
    WireModel,
)
from app.domain.vocabulary import INSTRUCTION_CHARS, ControlKind


class CreateRunRequest(WireModel):
    command_id: UUID
    supervisor_id: UUID
    order_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    initial_context: JsonObject
    demo_timing_preset: Literal["short_review", "short_expiry"] | None = None


class ReasonPayload(WireModel):
    reason: Message


class PaymentConfirmedPayload(WireModel):
    payment_reference: Reference | None = None
    attempt_reference: Reference | None = None


class PaymentFailedPayload(ReasonPayload):
    attempt_reference: Reference | None = None


class ShipmentCreatedPayload(WireModel):
    shipment_reference: Reference
    carrier: Reference | None = None
    expected_at: UTCDateTime | None = None


class ShipmentDelayedPayload(ReasonPayload):
    shipment_reference: Reference | None = None
    expected_at: UTCDateTime | None = None


class DeliveredPayload(WireModel):
    delivered_at: UTCDateTime | None = None
    evidence_reference: Reference | None = None

    @model_validator(mode="after")
    def evidence_required(self):
        if self.delivered_at is None and self.evidence_reference is None:
            raise ValueError("delivery requires a timestamp or evidence reference")
        return self


class RefundPayload(ReasonPayload):
    customer_reference: Reference | None = None


class CustomerMessagePayload(WireModel):
    message: Message


class NoUpdatePayload(WireModel):
    hours: Annotated[float, Field(strict=True, gt=0, le=8760, allow_inf_nan=False)]


KNOWN_PAYLOADS = {
    "payment_confirmed": PaymentConfirmedPayload,
    "payment_failed": PaymentFailedPayload,
    "shipment_created": ShipmentCreatedPayload,
    "shipment_delayed": ShipmentDelayedPayload,
    "delivered": DeliveredPayload,
    "refund_requested": RefundPayload,
    "customer_message_received": CustomerMessagePayload,
    "no_update_for_n_hours": NoUpdatePayload,
}


class EventCommand(WireModel):
    command_id: UUID
    event_id: Reference
    event_type: Annotated[
        str, StringConstraints(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    ]
    occurred_at: UTCDateTime
    payload: JsonObject

    @model_validator(mode="after")
    def validate_known_payload(self):
        payload_model = KNOWN_PAYLOADS.get(self.event_type)
        if payload_model:
            payload_model.model_validate(self.payload)
        # order_created and unfamiliar types retain a bounded context object.
        return self


class PolicyChanges(WireModel):
    prioritize_speed: bool | None = None
    escalate_shipment_delays: bool | None = None
    require_customer_review: bool | None = None


class InstructionCommand(WireModel):
    command_id: UUID
    operation: Literal["add", "supersede", "remove"]
    instruction_id: UUID | None = None
    text: (
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=INSTRUCTION_CHARS),
        ]
        | None
    ) = None
    policy_changes: PolicyChanges | None = None

    @model_validator(mode="after")
    def operation_shape(self):
        if (self.operation == "add") != (self.instruction_id is None):
            raise ValueError("supersede/remove require an existing instruction_id; add does not")
        if self.operation == "remove":
            if self.text is not None or self.policy_changes is not None:
                raise ValueError("remove cannot introduce text or policy changes")
        elif self.text is None:
            raise ValueError("add/supersede require text")
        return self


class ControlCommand(WireModel):
    command_id: UUID
    kind: ControlKind
    reason: ShortText | None = None


class ReviewCommand(WireModel):
    command_id: UUID
    draft_id: Reference
    content_digest: Digest
    decision: Literal["approve", "reject"]


class CommandAcknowledgement(WireModel):
    command_id: UUID
    run_id: UUID
    acceptance: Literal["accepted"] = "accepted"
    processing: Literal["pending"] = "pending"


class ApiError(WireModel):
    code: Reference
    message: ShortText
    retryable: bool
    field_details: dict[str, str] = Field(default_factory=dict)
    command_id: UUID | None = None
    run_id: UUID | None = None
