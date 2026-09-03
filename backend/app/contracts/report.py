"""The closing-report boundary.

Reporting is deliberately a different shape from a decision. The agent is asked for prose
over evidence it cannot change: there is no action vocabulary here, no sleep, no memory,
and no way to influence the closure reason. What comes back is three pieces of text, and
the workflow keeps its own factual version if any of them cannot be trusted.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.contracts.common import (
    Count,
    Message,
    Reference,
    ShortText,
    UTCDateTime,
    WireModel,
)
from app.contracts.decision import ProviderUsage
from app.contracts.run import CommittedAction, RefusedAction, RunSnapshot
from app.domain.vocabulary import CloseReason

# Deliberately smaller than the final record's ten: a closing note that lists ten
# learnings is padding, and the deterministic observations already occupy some room.
NARRATIVE_ITEMS = 6


class ReportNarrative(WireModel):
    """The only part of a closing report a model may write."""

    summary: Message
    learnings: Annotated[list[ShortText], Field(max_length=NARRATIVE_ITEMS)] = Field(
        default_factory=list
    )
    feedback: Annotated[list[ShortText], Field(max_length=NARRATIVE_ITEMS)] = Field(
        default_factory=list
    )


class ReportEvidenceRequest(WireModel):
    """Every action receipt this run recorded, up to the report's frozen cutoff.

    Unlike an evidence read for a decision, this one is a scan of a single kind for a
    single run — the report has to list *all* receipts, not the bounded working ledger a
    decision carries.
    """

    run_id: UUID
    through_sequence: Count


class ReportEvidence(WireModel):
    committed: Annotated[list[CommittedAction], Field(max_length=128)] = Field(
        default_factory=list
    )
    refused: Annotated[list[RefusedAction], Field(max_length=64)] = Field(default_factory=list)
    # Rows the reader could not turn into a receipt. Reported rather than hidden, so a
    # short list is visibly short rather than silently short.
    unreadable: Count = 0
    # True when the run has more receipts than a report will list.
    truncated: bool = False


class ReportRequest(WireModel):
    """One bounded reporting call against a frozen cutoff.

    `factual` is the deterministic version that will be kept if this call fails or comes
    back contradicting the record, so the model is writing an alternative to something
    that already works rather than the only thing standing between the run and no report.
    """

    run_id: UUID
    close_reason: CloseReason
    closed_at: UTCDateTime
    evidence_through_sequence: Count
    snapshot: RunSnapshot
    committed: Annotated[list[CommittedAction], Field(max_length=128)] = Field(
        default_factory=list
    )
    refused: Annotated[list[RefusedAction], Field(max_length=64)] = Field(default_factory=list)
    factual: ReportNarrative


class ReportResult(WireModel):
    """What the reporting call produced, and whether it can be believed.

    `provenance` is `factual_fallback` whenever `narrative` is absent — including in
    scripted mode, where no model was asked at all. A stand-in is never presented as a
    model-written report.
    """

    narrative: ReportNarrative | None = None
    provenance: Literal["model_assisted", "factual_fallback"]
    model_label: Reference | None = None
    usage: ProviderUsage | None = None
    # Why the model's version was not used, when it was not.
    limitation: ShortText | None = None
    attempts: Count = 0
