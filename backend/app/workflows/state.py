"""What crosses a workflow boundary.

Two shapes start an execution. `WorkflowInput` starts a new order. `WorkflowCarry`
resumes one whose Temporal history grew long enough to roll over — a new execution of the
same application run, not a new run. The `kind` discriminator is what tells them apart,
because everything else about the two situations looks similar and getting it wrong would
mean replaying an order's initialisation on top of itself.

The carry is the continuity contract. Anything absent from it is genuinely lost at a
rollover, which is why pending commands, operator intent, and the counters that mint
identifiers are all here. What is deliberately *not* here is the audit timeline and the
history of delivered event IDs: PostgreSQL keeps the record, and its receipt lookup stays
the duplicate authority, so an old event redelivered after a rollover resolves the same
way it always would.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.common import Count, JsonObject, ShortText, UTCDateTime, WireModel
from app.contracts.run import RunSnapshot
from app.domain.vocabulary import PENDING_COMMANDS, CloseReason, ControlKind, DecisionTrigger


class PendingCommand(WireModel):
    """An admitted command that has not been settled yet.

    Held as the raw envelope rather than a validated model on purpose: a malformed
    command still deserves to be recorded as rejected after the rollover, and validating
    it here would throw it away instead.
    """

    kind: Literal["event", "instruction", "control", "review"]
    command: JsonObject


class ControlLatch(WireModel):
    """Operator intent taken before admission, which must survive the boundary."""

    kind: ControlKind
    during_effect: bool = False


class ClosureLatch(WireModel):
    reason: CloseReason
    observed_at: UTCDateTime


class WorkflowInput(WireModel):
    kind: Literal["start"] = "start"
    schema_version: Literal[1] = 1
    initial_event_id: UUID
    snapshot: RunSnapshot


class WorkflowCarry(WireModel):
    kind: Literal["carry"] = "carry"
    schema_version: Literal[1] = 1
    initial_event_id: UUID
    # Identity, the frozen template, facts, instructions, the issue ledger, memory and
    # its cutoff, unresolved and deferred references, the next deadline, validated
    # guidance, pause state, and any pending draft all travel inside this.
    confirmed_snapshot: RunSnapshot
    # Counters that mint identifiers. Resetting these would reuse an operation or
    # decision ID and make a replayed write look like a fresh one.
    operation_counter: Count
    decision_counter: Count
    draft_counter: Count = 0
    stale_discards: Count = 0
    pending_commands: Annotated[list[PendingCommand], Field(max_length=PENDING_COMMANDS)] = Field(
        default_factory=list
    )
    pending_control_intents: Annotated[
        dict[str, ControlLatch], Field(max_length=PENDING_COMMANDS)
    ] = Field(default_factory=dict)
    terminal_pending: bool = False
    closure_latch: ClosureLatch | None = None
    # Why the next execution should assess, so a rollover resumes the pending reason for
    # work rather than repeating the initial-start trigger.
    pending_trigger: DecisionTrigger | None = None
    pending_trigger_detail: ShortText | None = None
