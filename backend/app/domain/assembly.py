"""Choosing what a decision actually gets to see.

A run accumulates more history than any single decision should carry, so something has to
choose. The rule here is that recency is not the only claim on attention: an unanswered
question from an hour ago outranks the third routine shipment update, and evidence that
was recorded but never reasoned over stays visible until a decision genuinely covers it.

Nothing is dropped silently. When the assembled context will not fit, the run says so and
asks for consolidation, because quietly discarding the oldest question is exactly the
failure that makes a long-running agent untrustworthy.
"""

import json
from dataclasses import dataclass, field

from app.contracts.decision import EvidenceDetail
from app.contracts.run import EvidenceReference, RunSnapshot
from app.domain.vocabulary import CONTEXT_BUDGET_BYTES, DEFERRED_EVIDENCE_LIMIT, RECENT_RECORDS


@dataclass(frozen=True)
class EvidencePlan:
    """Which sequences to fetch, and which of them are still awaiting consideration."""

    considered: tuple[int, ...] = ()
    unconsidered: tuple[int, ...] = ()

    @property
    def sequences(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.considered) | set(self.unconsidered)))


@dataclass(frozen=True)
class Assembled:
    considered: list[EvidenceDetail] = field(default_factory=list)
    unconsidered: list[EvidenceDetail] = field(default_factory=list)


def _sequences(references: list[EvidenceReference]) -> list[int]:
    return [reference.sequence for reference in references]


def plan(snapshot: RunSnapshot) -> EvidencePlan:
    """Decide what this decision needs to read.

    Unconsidered evidence is anything recorded past the last decision's coverage — the
    deferred references the policy chose not to wake for, plus whatever is attached to a
    concern that is still open. Those are bounded separately from the recent window so a
    burst of routine updates cannot push an unanswered question out of view.
    """
    boundary = snapshot.last_decision_through_sequence
    pending = {
        sequence
        for sequence in _sequences(snapshot.deferred_evidence) + _sequences(
            snapshot.unresolved_evidence
        )
        if sequence > boundary
    }
    unconsidered = tuple(sorted(pending)[-DEFERRED_EVIDENCE_LIMIT:])
    recent = tuple(sorted(set(_sequences(snapshot.recent_evidence)))[-RECENT_RECORDS:])
    return EvidencePlan(
        considered=tuple(sequence for sequence in recent if sequence not in pending),
        unconsidered=unconsidered,
    )


def split(plan: EvidencePlan, records: list[EvidenceDetail]) -> Assembled:
    """Sort what came back into what has been reasoned over and what has not."""
    pending = set(plan.unconsidered)
    return Assembled(
        considered=[record for record in records if record.sequence not in pending],
        unconsidered=[record for record in records if record.sequence in pending],
    )


def serialized_size(payload: dict) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())


def over_budget(payload: dict) -> int | None:
    """Return the size when the assembled context is too large, or None when it fits."""
    size = serialized_size(payload)
    return size if size > CONTEXT_BUDGET_BYTES else None


def still_pending(snapshot: RunSnapshot, cutoff: int) -> list[dict]:
    """Deferred references a decision did not cover, because they arrived after its input.

    Marking everything considered because *a* decision ran would quietly bury an event
    that landed while the model was thinking.
    """
    return [
        reference.model_dump(mode="json")
        for reference in snapshot.deferred_evidence
        if reference.sequence > cutoff
    ]
