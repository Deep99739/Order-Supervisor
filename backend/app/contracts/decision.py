from typing import Annotated, Literal

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
from app.contracts.run import ContextStamp, RunSnapshot
from app.domain.vocabulary import (
    ACTION_BATCH,
    SUMMARY_CHARS,
    ActionName,
    DecisionTrigger,
    KnownEvent,
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


class WakeHint(WireModel):
    kind: Literal["watch_for_progress", "shorten_review", "await_response"]
    issue_id: Reference
    expires_at: UTCDateTime
    event_type: KnownEvent | None = None
    review_after_seconds: Annotated[int, Field(strict=True, ge=10, le=3600)] | None = None

    @model_validator(mode="after")
    def supported_hint(self):
        if self.kind == "watch_for_progress" and self.event_type is None:
            raise ValueError("watch_for_progress requires a known event type")
        if self.kind == "shorten_review" and self.review_after_seconds is None:
            raise ValueError("shorten_review requires a bounded interval")
        return self


class WakeGuidance(WireModel):
    version: PositiveInt
    context: ContextStamp
    hints: Annotated[list[WakeHint], Field(max_length=5)]


class DecisionRequest(WireModel):
    """One bounded decision episode. Provider retries stay attempts of this episode."""

    decision_id: Reference
    trigger: DecisionTrigger
    attempt: PositiveInt
    context: ContextStamp
    snapshot: RunSnapshot
    trigger_detail: ShortText


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
