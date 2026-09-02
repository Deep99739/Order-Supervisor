"""The compact memory summary.

This phase renders it deterministically from confirmed facts, so a run always has a
readable summary that cannot claim anything the record does not support. A model-proposed
refresh, its evidence cutoff, and selective retention arrive with the memory phase.
"""

from app.contracts.run import RunSnapshot
from app.domain.vocabulary import SUMMARY_CHARS

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


def render_summary(snapshot: RunSnapshot) -> str:
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
