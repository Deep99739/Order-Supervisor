"""Pure event interpretation.

Validation already happened at the HTTP edge. This module answers a different question:
given what the order is currently known to be, what does this event actually mean?

Two rules shape everything here. Newer confirmed facts are never silently reversed by
older evidence — contradictions are retained and surfaced instead. And an event that
cannot be reconciled becomes a concern requiring review rather than a quiet fact change.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from app.contracts.commands import EventCommand
from app.contracts.run import EvidenceReference, OpenIssue, OrderFacts, RunSnapshot
from app.domain.vocabulary import EVIDENCE_REFERENCES, KnownEvent

Importance = Literal["important", "routine"]

PAYMENT_ISSUE = "payment"
PAYMENT_EVIDENCE_ISSUE = "payment-evidence"
SHIPMENT_DELAY_ISSUE = "shipment-delay"
SHIPMENT_EVIDENCE_ISSUE = "shipment-evidence"
STALLED_ISSUE = "stalled"
REFUND_ISSUE = "refund"
CUSTOMER_ISSUE = "customer"

# Issues that delivery genuinely settles. A refund question or an unknown event does not
# become resolved just because the parcel arrived.
DELIVERY_RESOLVES = (SHIPMENT_DELAY_ISSUE, SHIPMENT_EVIDENCE_ISSUE, STALLED_ISSUE)

# Events that count as order progress for the inactivity check. A customer question or a
# refund request is a concern, not evidence that the order moved forward.
PROGRESS_EVENTS = frozenset(
    {
        KnownEvent.PAYMENT_CONFIRMED,
        KnownEvent.PAYMENT_FAILED,
        KnownEvent.SHIPMENT_CREATED,
        KnownEvent.SHIPMENT_DELAYED,
        KnownEvent.DELIVERED,
    }
)


@dataclass(frozen=True)
class EventOutcome:
    facts: OrderFacts
    explanation: str
    importance: Importance
    material: bool
    review_required: bool = False
    terminal: bool = False


def _facts(facts: OrderFacts, **changes: Any) -> OrderFacts:
    return OrderFacts.model_validate(facts.model_dump(mode="json") | changes)


def _issues(facts: OrderFacts) -> list[dict[str, Any]]:
    return [issue.model_dump(mode="json") for issue in facts.open_issues]


def open_issue(
    facts: OrderFacts,
    issue_id: str,
    description: str,
    evidence: EvidenceReference,
    *,
    review_required: bool = False,
) -> OrderFacts:
    """Open or update one issue. Repeat delivery updates it rather than stacking copies."""
    issues = _issues(facts)
    reference = evidence.model_dump(mode="json")
    for issue in issues:
        if issue["issue_id"] == issue_id:
            issue["description"] = description
            issue["review_required"] = issue["review_required"] or review_required
            history = [item for item in issue["evidence"] if item != reference]
            issue["evidence"] = (history + [reference])[-12:]
            return _facts(facts, open_issues=issues)
    if len(issues) >= EVIDENCE_REFERENCES:
        return facts
    issues.append(
        OpenIssue(
            issue_id=issue_id,
            description=description,
            evidence=[evidence],
            review_required=review_required,
        ).model_dump(mode="json")
    )
    return _facts(facts, open_issues=issues)


def resolve_issues(facts: OrderFacts, *issue_ids: str) -> OrderFacts:
    remaining = [issue for issue in _issues(facts) if issue["issue_id"] not in issue_ids]
    return _facts(facts, open_issues=remaining)


def has_issue(facts: OrderFacts, issue_id: str) -> bool:
    return any(issue.issue_id == issue_id for issue in facts.open_issues)


def unknown_issue_id(event_type: str) -> str:
    return f"unknown:{event_type}"


def _older_than(moment: datetime | None, recorded: datetime | None) -> bool:
    """True when this evidence predates what is already recorded."""
    return moment is not None and recorded is not None and moment < recorded


def interpret(
    snapshot: RunSnapshot, command: EventCommand, *, now: datetime, evidence: EvidenceReference
) -> EventOutcome:
    facts = snapshot.facts
    payload = command.payload
    occurred = command.occurred_at
    progress = occurred if command.event_type in PROGRESS_EVENTS else None

    if command.event_type == KnownEvent.ORDER_CREATED:
        return EventOutcome(
            facts,
            "Creation was already recorded when supervision started.",
            "routine",
            material=False,
        )

    if command.event_type == KnownEvent.PAYMENT_CONFIRMED:
        return _payment_confirmed(facts, payload, occurred, progress)

    if command.event_type == KnownEvent.PAYMENT_FAILED:
        return _payment_failed(facts, payload, occurred, progress, evidence)

    if command.event_type == KnownEvent.SHIPMENT_CREATED:
        return _shipment_created(facts, payload, occurred, progress)

    if command.event_type == KnownEvent.SHIPMENT_DELAYED:
        return _shipment_delayed(facts, payload, occurred, progress, evidence)

    if command.event_type == KnownEvent.DELIVERED:
        return _delivered(facts, payload, occurred, progress)

    if command.event_type == KnownEvent.REFUND_REQUESTED:
        reason = str(payload.get("reason", "A refund was requested."))
        return EventOutcome(
            open_issue(facts, REFUND_ISSUE, f"Refund requested: {reason}"[:500], evidence),
            "Refund request recorded. Nothing about this event says a refund was approved or paid.",
            "important",
            material=True,
        )

    if command.event_type == KnownEvent.CUSTOMER_MESSAGE_RECEIVED:
        message = str(payload.get("message", ""))
        return EventOutcome(
            open_issue(
                facts, CUSTOMER_ISSUE, f"Customer wrote: {message}"[:500], evidence
            ),
            "Customer message recorded as evidence. Customer text never becomes an instruction.",
            "important",
            material=True,
        )

    if command.event_type == KnownEvent.NO_UPDATE_FOR_N_HOURS:
        return _no_update(snapshot, facts, payload, now, evidence)

    return _unknown(facts, command, evidence)


def _payment_confirmed(facts, payload, occurred, progress) -> EventOutcome:
    if facts.payment == "confirmed":
        return EventOutcome(facts, "Payment was already confirmed.", "routine", material=False)
    if _older_than(occurred, facts.payment_observed_at):
        return EventOutcome(
            facts,
            "Older payment evidence arrived after a newer payment state; the newer state stands.",
            "routine",
            material=False,
        )
    resolving = has_issue(facts, PAYMENT_ISSUE)
    updated = resolve_issues(facts, PAYMENT_ISSUE) if resolving else facts
    updated = _facts(
        updated,
        payment="confirmed",
        payment_attempt_reference=payload.get("attempt_reference")
        or payload.get("payment_reference")
        or facts.payment_attempt_reference,
        payment_observed_at=occurred,
        last_relevant_progress_at=progress or facts.last_relevant_progress_at,
    )
    if resolving:
        return EventOutcome(
            updated, "Payment confirmed; the open payment issue is resolved.", "important", True
        )
    return EventOutcome(updated, "Payment confirmed.", "routine", material=True)


def _payment_failed(facts, payload, occurred, progress, evidence) -> EventOutcome:
    reason = str(payload.get("reason", "no reason supplied"))
    attempt = payload.get("attempt_reference")
    if facts.payment == "confirmed":
        same_attempt = (
            attempt is not None
            and facts.payment_attempt_reference is not None
            and attempt == facts.payment_attempt_reference
        )
        if same_attempt and _older_than(occurred, facts.payment_observed_at):
            return EventOutcome(
                facts,
                "An older failure for the confirmed attempt cannot reverse the newer confirmation.",
                "routine",
                material=False,
            )
        if not same_attempt:
            # Ambiguous evidence never silently undoes recorded payment facts.
            conflict = (
                f"Payment failure for an unmatched attempt while payment is confirmed: {reason}"
            )
            return EventOutcome(
                open_issue(
                    facts,
                    PAYMENT_EVIDENCE_ISSUE,
                    conflict[:500],
                    evidence,
                    review_required=True,
                ),
                "Payment failure could not be matched to the confirmed attempt; it needs review.",
                "important",
                material=True,
                review_required=True,
            )
    updated = open_issue(facts, PAYMENT_ISSUE, f"Payment failed: {reason}"[:500], evidence)
    updated = _facts(
        updated,
        payment="failed",
        payment_attempt_reference=attempt or facts.payment_attempt_reference,
        payment_observed_at=occurred,
        last_relevant_progress_at=progress or facts.last_relevant_progress_at,
    )
    return EventOutcome(
        updated, "Payment failed; the order is not cancelled by this event.", "important", True
    )


def _shipment_created(facts, payload, occurred, progress) -> EventOutcome:
    if facts.shipment == "delivered":
        return EventOutcome(
            facts,
            "Delivery is already recorded; older shipment evidence does not regress it.",
            "routine",
            material=False,
        )
    if _older_than(occurred, facts.shipment_observed_at):
        return EventOutcome(
            facts,
            "Older shipment evidence arrived after newer progress; the newer state stands.",
            "routine",
            material=False,
        )
    resolving = has_issue(facts, STALLED_ISSUE)
    updated = resolve_issues(facts, STALLED_ISSUE) if resolving else facts
    updated = _facts(
        updated,
        shipment="in_transit",
        shipment_reference=payload.get("shipment_reference") or facts.shipment_reference,
        shipment_observed_at=occurred,
        expected_at=payload.get("expected_at") or facts.expected_at,
        last_relevant_progress_at=progress or facts.last_relevant_progress_at,
    )
    if resolving:
        return EventOutcome(
            updated,
            "Shipment created; the overdue-progress concern is resolved.",
            "important",
            material=True,
        )
    return EventOutcome(updated, "Shipment created.", "routine", material=True)


def _shipment_delayed(facts, payload, occurred, progress, evidence) -> EventOutcome:
    reason = str(payload.get("reason", "no reason supplied"))
    reference = payload.get("shipment_reference")
    if facts.shipment == "delivered":
        return EventOutcome(
            facts,
            "Delivery is already recorded; a delay for this order no longer changes its state.",
            "routine",
            material=False,
        )
    if reference and facts.shipment_reference and reference != facts.shipment_reference:
        return EventOutcome(
            open_issue(
                facts,
                SHIPMENT_EVIDENCE_ISSUE,
                f"Delay reported for shipment {reference}, but {facts.shipment_reference} "
                f"is recorded for this order: {reason}"[:500],
                evidence,
                review_required=True,
            ),
            "The delay names a different shipment than the one recorded; it needs review.",
            "important",
            material=True,
            review_required=True,
        )
    updated = open_issue(facts, SHIPMENT_DELAY_ISSUE, f"Shipment delayed: {reason}"[:500], evidence)
    updated = _facts(
        updated,
        shipment="delayed",
        shipment_reference=reference or facts.shipment_reference,
        shipment_observed_at=occurred,
        expected_at=payload.get("expected_at") or facts.expected_at,
        last_relevant_progress_at=progress or facts.last_relevant_progress_at,
    )
    return EventOutcome(updated, "Shipment delayed.", "important", material=True)


def _delivered(facts, payload, occurred, progress) -> EventOutcome:
    updated = resolve_issues(facts, *DELIVERY_RESOLVES)
    updated = _facts(
        updated,
        shipment="delivered",
        delivered_at=payload.get("delivered_at") or occurred,
        delivery_evidence_reference=payload.get("evidence_reference"),
        shipment_observed_at=occurred,
        last_relevant_progress_at=progress or facts.last_relevant_progress_at,
    )
    return EventOutcome(
        updated,
        "Delivery recorded. Supervision closes under the delivery rule; other concerns stay open.",
        "important",
        material=True,
        terminal=True,
    )


def _no_update(snapshot, facts, payload, now, evidence) -> EventOutcome:
    hours = float(payload["hours"])
    # Measured against recorded order progress, never against the last event received —
    # otherwise repeated probes would reset the very clock they are meant to check.
    since = facts.last_relevant_progress_at or snapshot.started_at
    elapsed = now - since
    if elapsed < timedelta(hours=hours):
        minutes = int(elapsed.total_seconds() // 60)
        return EventOutcome(
            facts,
            f"Progress was recorded {minutes} minutes ago, so the reported {hours}h gap "
            "has not elapsed.",
            "routine",
            material=False,
        )
    return EventOutcome(
        open_issue(
            facts,
            STALLED_ISSUE,
            f"No order progress recorded for {hours} hours since {since.isoformat()}."[:500],
            evidence,
        ),
        f"No progress for {hours} hours; the order needs a review.",
        "important",
        material=True,
    )


def _unknown(facts, command: EventCommand, evidence) -> EventOutcome:
    return EventOutcome(
        open_issue(
            facts,
            unknown_issue_id(command.event_type),
            f"Unrecognised event '{command.event_type}' received and kept as evidence."[:500],
            evidence,
            review_required=True,
        ),
        f"'{command.event_type}' is not a known event type; it is kept as evidence for review.",
        "important",
        material=True,
        review_required=True,
    )
