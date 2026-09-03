"""The compact narrative, and the rules about what it is allowed to be.

Two ideas do most of the work here. A summary has an **evidence cutoff**, so it is never
mistaken for a complete account of the run — anything recorded after that sequence is
outside what the text covers. And compaction is **triggered**, not continuous: a run that
resummarises on every event is doing work nobody asked for and producing a version number
that means nothing.

What the narrative may never do is stand in for authority. Facts come from event
reducers, restrictions come from operator commands, and an effect comes from a receipt.
The summary explains; it does not establish. That is why a model-written one is labelled
`model` and applied only to the range it declares.
"""

from dataclasses import dataclass
from datetime import datetime

from app.contracts.decision import MemoryRefresh
from app.contracts.run import MemorySummary, RunSnapshot
from app.domain.vocabulary import COMPACTION_RECORDS, DEMO_COMPACTION_RECORDS, SUMMARY_CHARS

PAYMENT_TEXT = {
    "unknown": "Payment state is not yet known",
    "pending": "Payment is pending",
    "confirmed": "Payment is confirmed",
    "failed": "Payment has failed",
}

SHIPMENT_TEXT = {
    "unknown": "no shipment information yet",
    "not_created": "no shipment created yet",
    "in_transit": "shipment in transit",
    "delayed": "shipment delayed",
    "delivered": "delivered",
}


@dataclass(frozen=True)
class Compaction:
    """One accepted refresh, with everything an audit record needs."""

    summary: MemorySummary
    before_chars: int
    covered_from: int
    reason: str

    @property
    def after_chars(self) -> int:
        return len(self.summary.text)


@dataclass(frozen=True)
class RefusedRefresh:
    reason: str
    explanation: str


def render_summary(snapshot: RunSnapshot) -> str:
    """A factual narrative derived only from confirmed state.

    This is always available. It cannot be wrong about the order, because it asserts
    nothing the snapshot does not already hold.
    """
    facts = snapshot.facts
    parts = [f"Order {snapshot.order_id} under {snapshot.supervisor.name}."]

    shipment = SHIPMENT_TEXT.get(facts.shipment, facts.shipment)
    if facts.shipment_reference:
        shipment = f"{shipment} ({facts.shipment_reference})"
    parts.append(f"{PAYMENT_TEXT.get(facts.payment, facts.payment)}; {shipment}.")

    if facts.delivered_at:
        parts.append(f"Delivered {facts.delivered_at.isoformat()}.")
    elif facts.expected_at:
        parts.append(f"Expected {facts.expected_at.isoformat()}.")

    if facts.last_relevant_progress_at:
        parts.append(f"Last recorded progress {facts.last_relevant_progress_at.isoformat()}.")

    if snapshot.committed_actions:
        recent = snapshot.committed_actions[-3:]
        listed = "; ".join(f"{item.action} ({item.action_id})" for item in recent)
        parts.append(f"Recorded so far — {listed}.")

    if facts.open_issues:
        listed = "; ".join(
            f"{issue.issue_id}: {issue.description}"
            + (" (needs review)" if issue.review_required else "")
            for issue in facts.open_issues
        )
        parts.append(f"Open work — {listed}.")
    else:
        parts.append("No open issues recorded.")

    if snapshot.instructions:
        parts.append(f"{len(snapshot.instructions)} standing instruction(s) apply.")

    summary = " ".join(parts)
    if len(summary) <= SUMMARY_CHARS:
        return summary
    # Trim whole sentences rather than slicing one in half.
    trimmed = parts[0]
    for part in parts[1:]:
        if len(trimmed) + len(part) + 1 > SUMMARY_CHARS:
            break
        trimmed = f"{trimmed} {part}"
    return trimmed[:SUMMARY_CHARS]


def compaction_threshold(snapshot: RunSnapshot) -> int:
    if snapshot.supervisor.wake_profile.mode == "demo":
        return DEMO_COMPACTION_RECORDS
    return COMPACTION_RECORDS


def records_since_summary(snapshot: RunSnapshot) -> int:
    return max(snapshot.last_sequence - snapshot.memory.summary_through_sequence, 0)


def refresh_due(snapshot: RunSnapshot) -> bool:
    """Whether enough has happened since the cutoff to be worth resummarising.

    Deliberately not "has anything changed". Summarising on every event produces a
    version number that tracks nothing and a compaction count that means nothing.
    """
    if not snapshot.memory.text:
        return True
    return records_since_summary(snapshot) >= compaction_threshold(snapshot)


def deterministic(snapshot: RunSnapshot, *, now: datetime, reason: str) -> Compaction:
    """Re-render from confirmed state. Available always, including while held.

    Callers decide when this is due; it always produces a compaction, because even when
    the wording is unchanged the cutoff has moved and that is worth recording.
    """
    text = render_summary(snapshot)
    return Compaction(
        summary=MemorySummary(
            text=text,
            summary_version=snapshot.memory.summary_version + 1,
            summary_through_sequence=snapshot.last_sequence,
            recorded_at=now,
            provenance="deterministic",
        ),
        before_chars=len(snapshot.memory.text),
        covered_from=snapshot.memory.summary_through_sequence,
        reason=reason,
    )


def from_proposal(
    snapshot: RunSnapshot,
    refresh: MemoryRefresh,
    *,
    input_cutoff: int,
    decision_reference: str,
    now: datetime,
) -> Compaction | RefusedRefresh:
    """Adopt a model-written narrative, but only over the range it actually declares.

    Two ways a proposal is refused. Claiming to cover evidence the decision never
    received would make the cutoff a lie about what was read. Covering less than the
    existing summary already does would lose ground rather than compact anything. Neither
    is trimmed into shape: the previous valid summary and the deterministic renderer are
    both still there, so rejecting costs nothing.
    """
    if refresh.through_sequence > input_cutoff:
        return RefusedRefresh(
            "cutoff_beyond_input",
            f"The proposed summary claims to cover evidence through {refresh.through_sequence}, "
            f"but this decision only received evidence through {input_cutoff}.",
        )
    if refresh.through_sequence < snapshot.memory.summary_through_sequence:
        return RefusedRefresh(
            "cutoff_regressed",
            f"The proposed summary covers less ({refresh.through_sequence}) than the summary "
            f"already recorded ({snapshot.memory.summary_through_sequence}).",
        )
    return Compaction(
        summary=MemorySummary(
            text=refresh.text,
            summary_version=snapshot.memory.summary_version + 1,
            summary_through_sequence=refresh.through_sequence,
            recorded_at=now,
            provenance="model",
            source_decision_id=decision_reference,
        ),
        before_chars=len(snapshot.memory.text),
        covered_from=snapshot.memory.summary_through_sequence,
        reason="The agent proposed a summary during a review that was already required.",
    )
