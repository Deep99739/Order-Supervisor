"""Versioned edge validation only. Pass JSON primitives to Temporal's default converter.

This carry represents a safe continuation boundary: no authorized transaction or
decision activity may still be in flight. It does not implement continuation itself.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.commands import ControlCommand, EventCommand, InstructionCommand, ReviewCommand
from app.contracts.common import Count, UTCDateTime, WireModel
from app.contracts.decision import WakeGuidance
from app.contracts.run import RunSnapshot
from app.domain.vocabulary import PENDING_COMMANDS, CloseReason, ControlKind


class PendingEvent(WireModel):
    kind: Literal["event"]
    command: EventCommand


class PendingInstruction(WireModel):
    kind: Literal["instruction"]
    command: InstructionCommand


class PendingControl(WireModel):
    kind: Literal["control"]
    command: ControlCommand


class PendingReview(WireModel):
    kind: Literal["review"]
    command: ReviewCommand


PendingCommand = Annotated[
    PendingEvent | PendingInstruction | PendingControl | PendingReview, Field(discriminator="kind")
]


class WorkflowInput(WireModel):
    schema_version: Literal[1] = 1
    initial_event_id: UUID
    snapshot: RunSnapshot


class ClosureLatch(WireModel):
    reason: CloseReason
    observed_at: UTCDateTime


class WorkflowCarry(WireModel):
    schema_version: Literal[1] = 1
    initial_event_id: UUID
    confirmed_snapshot: RunSnapshot
    operation_counter: Count
    decision_counter: Count
    pending_commands: Annotated[list[PendingCommand], Field(max_length=PENDING_COMMANDS)] = Field(
        default_factory=list
    )
    pending_control_intents: Annotated[
        dict[UUID, ControlKind], Field(max_length=PENDING_COMMANDS)
    ] = Field(default_factory=dict)
    wake_guidance: WakeGuidance | None = None
    closure_latch: ClosureLatch | None = None
    pending_trigger: (
        Literal["start", "important_event", "scheduled_wake", "instruction", "resume", "recovery"]
        | None
    ) = None
