from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    Count,
    Message,
    PositiveInt,
    Reference,
    ShortText,
    Subject,
    UTCDateTime,
    WireModel,
)
from app.contracts.run import (
    ActivityDisposition,
    ActivityKind,
    ContextStamp,
    RunSnapshot,
    WakeGuidance,
)
from app.domain.vocabulary import (
    ACTION_BATCH,
    SUMMARY_CHARS,
    ActionName,
    DecisionTrigger,
    NoteCategory,
)


class ActionProposal(WireModel):
    """What the agent asks for. Which fields an individual action actually requires is
    the registry's business, so this stays the transport shape and nothing more."""

    action: ActionName
    content: Message
    subject: Subject | None = None
    category: NoteCategory | None = None
    issue_id: Reference | None = None
    rationale: ShortText


class MemoryRefresh(WireModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=SUMMARY_CHARS)]
    through_sequence: Count


class EvidenceDetail(WireModel):
    """One recorded entry, retrieved by sequence so a decision can see the input itself
    rather than a reference to it. The stored `details` payload is deliberately not
    carried: an explanation is what a decision needs, and envelopes are large."""

    sequence: PositiveInt
    kind: ActivityKind
    disposition: ActivityDisposition
    recorded_at: UTCDateTime
    occurred_at: UTCDateTime | None = None
    event_id: Reference | None = None
    action_id: Reference | None = None
    explanation: ShortText


class EvidenceRequest(WireModel):
    """A read of the activity log by known sequence. No search, no scan."""

    run_id: UUID
    sequences: Annotated[list[Count], Field(max_length=64)]


class EvidenceBundle(WireModel):
    records: Annotated[list[EvidenceDetail], Field(max_length=64)] = Field(default_factory=list)
    # Sequences that were asked for but no longer exist; a missing row is not an error.
    missing: Count = 0


class DecisionRequest(WireModel):
    """One bounded decision episode. Provider retries stay attempts of this episode."""

    decision_id: Reference
    trigger: DecisionTrigger
    attempt: PositiveInt
    context: ContextStamp
    snapshot: RunSnapshot
    trigger_detail: ShortText
    # Defaulted so a request built before evidence assembly existed still validates.
    considered: Annotated[list[EvidenceDetail], Field(max_length=64)] = Field(default_factory=list)
    unconsidered: Annotated[list[EvidenceDetail], Field(max_length=64)] = Field(
        default_factory=list
    )


class DecisionProposal(WireModel):
    rationale: Message
    actions: Annotated[list[ActionProposal], Field(max_length=ACTION_BATCH)] = Field(
        default_factory=list
    )
    sleep_for_seconds: Annotated[int, Field(strict=True, ge=10, le=3600)] | None = None
    sleep_until: UTCDateTime | None = None
    memory_refresh: MemoryRefresh | None = None
    wake_guidance: WakeGuidance | None = None
    completion_recommendation: ShortText | None = None

    @model_validator(mode="after")
    def bounded_proposal(self):
        if self.sleep_for_seconds is not None and self.sleep_until is not None:
            raise ValueError("propose a duration OR a timestamp, not both")
        if sum(action.action == ActionName.MESSAGE_CUSTOMER for action in self.actions) > 1:
            raise ValueError("only one customer-message draft per decision")
        return self


class ProviderUsage(WireModel):
    """Only what the provider actually reported. An unreported number stays absent
    rather than being estimated, and transport attempts are counted separately from the
    episode's reasoning attempts."""

    input_tokens: Count | None = None
    output_tokens: Count | None = None
    transport_attempts: PositiveInt = 1


class DecisionResult(WireModel):
    """The proposal plus where it came from. A scripted result is never a model result."""

    proposal: DecisionProposal
    provenance: Literal["scripted", "model"]
    model_label: Reference | None = None
    usage: ProviderUsage | None = None
