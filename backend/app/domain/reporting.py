"""What a closing report may say, and how it is checked.

Two jobs live here, both pure so they can be exercised without Temporal or a provider.

`factual` renders a complete closing record from receipts and recorded facts alone. It is
what the run keeps when nothing else works, so it is never a placeholder: the assignment's
four sections are all present, populated from counts a reader can verify in the timeline.

`contradictions` is the gate a model-written narrative has to pass. It does not judge
prose. It looks for a small set of claims that the structured record can positively
refute — a payment that is not confirmed, a delivery that did not happen, a refund nobody
could have issued, an audience with no receipt — and returns the reasons. Any reason at
all means the factual version stands. Falling back costs a paragraph; believing an
invented completion costs the whole point of the record.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.contracts.report import ReportNarrative
from app.contracts.run import (
    CommittedAction,
    CustomerDraft,
    FinalOutput,
    RefusedAction,
    RunSnapshot,
)
from app.domain import actions as action_registry
from app.domain.vocabulary import ActionAudience, BlockReason, CloseReason

ENDED = {
    CloseReason.DELIVERED: "Delivery was recorded.",
    CloseReason.MANUALLY_TERMINATED: "An operator ended supervision.",
    CloseReason.MAXIMUM_AGE_REACHED: "The order reached its original maximum age.",
}

# Sentences are the unit of judgement: a verb and an audience that appear together are
# making one claim, whereas the same two words paragraphs apart are not.
SENTENCE = re.compile(r"[^.!?\n]+")

CONTACT_VERBS = (
    "messaged",
    "notified",
    "contacted",
    "informed",
    "told",
    "wrote to",
    "reached out",
    "chased",
    "emailed",
    "sent a message",
    "sent an update",
)

AUDIENCE_WORDS: dict[ActionAudience, tuple[str, ...]] = {
    ActionAudience.FULFILLMENT_TEAM: ("fulfillment", "fulfilment"),
    ActionAudience.PAYMENTS_TEAM: ("payments team", "payment team", "payments"),
    ActionAudience.LOGISTICS_TEAM: ("logistics", "carrier team"),
    ActionAudience.CUSTOMER: ("the customer", "customer"),
}

# Claims no recorded action in this system can ever support. Every effect here is a row
# in a table; nothing leaves the process.
IMPOSSIBLE = (
    ("refund was processed", "no action in this system can process a refund"),
    ("refund was issued", "no action in this system can issue a refund"),
    ("refund has been issued", "no action in this system can issue a refund"),
    ("issued a refund", "no action in this system can issue a refund"),
    ("processed the refund", "no action in this system can process a refund"),
    ("refunded the customer", "no action in this system can refund anyone"),
    ("sent an email", "actions are recorded, never sent"),
    ("an email was sent", "actions are recorded, never sent"),
    ("phoned", "actions are recorded, never sent"),
    ("called the customer", "actions are recorded, never sent"),
    ("text message", "actions are recorded, never sent"),
    ("reshipped", "no action in this system can ship anything"),
    ("replacement was shipped", "no action in this system can ship anything"),
    ("cancelled the order", "no action in this system can cancel an order"),
    ("canceled the order", "no action in this system can cancel an order"),
)

PAYMENT_CLAIMS = (
    "payment was confirmed",
    "payment is confirmed",
    "payment has been confirmed",
    "payment confirmed",
    "payment succeeded",
    "the order was paid",
)

DELIVERY_CLAIMS = (
    "was delivered",
    "has been delivered",
    "delivery was completed",
    "delivery completed",
    "the parcel arrived",
    "order arrived",
)

RESOLVED_CLAIMS = (
    "nothing remains unresolved",
    "no concerns remain",
    "all concerns were resolved",
    "all issues were resolved",
    "everything was resolved",
    "nothing outstanding",
)


def _sentences(text: str) -> list[str]:
    return [match.group(0).strip().casefold() for match in SENTENCE.finditer(text)]


def _text(narrative: ReportNarrative) -> str:
    return "\n".join([narrative.summary, *narrative.learnings, *narrative.feedback])


def contradictions(
    narrative: ReportNarrative,
    snapshot: RunSnapshot,
    committed: list[CommittedAction],
) -> tuple[str, ...]:
    """Reasons this narrative disagrees with the record. Empty means it may be used."""
    body = _text(narrative)
    lowered = body.casefold()
    facts = snapshot.facts
    found: list[str] = []

    for phrase, why in IMPOSSIBLE:
        if phrase in lowered:
            found.append(f"claims “{phrase}” — {why}")

    if facts.payment != "confirmed":
        for phrase in PAYMENT_CLAIMS:
            if phrase in lowered:
                found.append(f"claims “{phrase}” while payment is recorded as {facts.payment}")
                break

    if facts.shipment != "delivered":
        for phrase in DELIVERY_CLAIMS:
            if phrase in lowered:
                found.append(f"claims “{phrase}” while shipment is recorded as {facts.shipment}")
                break

    if facts.open_issues:
        for phrase in RESOLVED_CLAIMS:
            if phrase in lowered:
                names = ", ".join(issue.issue_id for issue in facts.open_issues)
                found.append(f"claims “{phrase}” while {names} remain open")
                break

    # An audience named alongside a contact verb has to have a receipt behind it.
    reached = {action_registry.audience_of(action.action) for action in committed}
    for sentence in _sentences(body):
        if not any(verb in sentence for verb in CONTACT_VERBS):
            continue
        for audience, words in AUDIENCE_WORDS.items():
            if audience in reached:
                continue
            if any(word in sentence for word in words):
                found.append(
                    f"says the {audience} was contacted, but no committed receipt reached them"
                )
                break

    return tuple(dict.fromkeys(found))


def factual(
    snapshot: RunSnapshot,
    reason: CloseReason,
    *,
    now: datetime,
    committed: list[CommittedAction],
    refused: list[RefusedAction],
    abandoned: CustomerDraft | None = None,
) -> FinalOutput:
    """A complete closing record built only from what was actually recorded.

    Every action listed has a receipt. Nothing merely proposed appears among them, a
    refused proposal is reported as refused, and a concern that was never settled is
    reported as still open — delivery does not resolve a refund question.
    """
    facts = snapshot.facts
    unresolved = list(facts.open_issues)
    narrative = _factual_narrative(
        snapshot, reason, committed=committed, refused=refused, abandoned=abandoned
    )
    return FinalOutput(
        close_reason=reason,
        closed_at=now,
        facts=facts,
        summary=narrative.summary,
        important_actions=committed[:128],
        blocked_actions=refused[:64],
        unresolved_issues=unresolved,
        learnings=narrative.learnings,
        feedback=narrative.feedback,
        narrative_provenance="factual_fallback",
        narrative_limitation=(
            "These are counts and facts from the record; no model wrote this closing text."
        ),
        evidence_through_sequence=snapshot.last_sequence,
    )


def _factual_narrative(
    snapshot: RunSnapshot,
    reason: CloseReason,
    *,
    committed: list[CommittedAction],
    refused: list[RefusedAction],
    abandoned: CustomerDraft | None,
) -> ReportNarrative:
    facts = snapshot.facts
    counters = snapshot.counters
    unresolved = list(facts.open_issues)

    summary = (
        f"{ENDED[reason]} Payment is {facts.payment} and shipment is {facts.shipment}. "
        f"{len(committed)} simulated action(s) were recorded and "
        f"{len(unresolved)} concern(s) remain unresolved."
    )

    learnings = [
        f"{counters.unique_events} order event(s) were admitted across "
        f"{counters.decisions} review episode(s).",
        f"{counters.deferred_events} event(s) were recorded without waking the agent.",
    ]
    if counters.duplicate_events:
        learnings.append(f"{counters.duplicate_events} duplicate delivery(ies) were ignored.")
    if committed:
        audiences = sorted(
            {str(action_registry.audience_of(item.action)) for item in committed}
        )
        learnings.append(
            f"{len(committed)} action(s) were recorded, reaching: {', '.join(audiences)}."
        )
    else:
        learnings.append("No business action was needed or authorised during this run.")
    if refused:
        reasons = sorted({str(item.reason) for item in refused})
        learnings.append(
            f"{len(refused)} proposal(s) were not carried out: {', '.join(reasons)}."
        )
    if unresolved:
        learnings.append(
            "Unresolved at closure: " + ", ".join(issue.issue_id for issue in unresolved)
        )

    feedback = ["Every recorded action is a simulation; nothing was sent outside this system."]
    if unresolved:
        feedback.append("The unresolved concerns above need human follow-up.")
    contacted = [issue for issue in unresolved if issue.contacts]
    if contacted:
        feedback.append(
            "Already chased without resolution: "
            + ", ".join(issue.issue_id for issue in contacted)
            + ". A different audience or a person may be needed."
        )
    if any(item.reason is BlockReason.APPROVAL_REQUIRED for item in refused):
        feedback.append(
            "Customer contact waited on human approval during this run; review whether that "
            "gate belongs on this kind of order."
        )
    if abandoned is not None:
        feedback.append(
            f"A customer draft ({abandoned.draft_id}) was still {abandoned.status} when the "
            "run closed, so the customer was never written to about it."
        )

    return ReportNarrative(
        summary=summary[:2000],
        learnings=[item[:500] for item in learnings][:6],
        feedback=[item[:500] for item in feedback][:6],
    )


def adopt(
    base: FinalOutput,
    narrative: ReportNarrative,
    *,
    model_label: str | None,
) -> FinalOutput:
    """Swap a checked narrative into the factual record, changing nothing else.

    Only three text fields move. The closure reason, the facts, the receipts, the refused
    proposals, and the unresolved list are the record's, not the model's.
    """
    return FinalOutput.model_validate(
        base.model_dump(mode="json")
        | {
            "summary": narrative.summary,
            # The deterministic counts stay first; the model's observations follow, so a
            # reader can still check the numbers against the timeline.
            "learnings": (base.learnings[:4] + list(narrative.learnings))[:10],
            "feedback": (base.feedback[:4] + list(narrative.feedback))[:10],
            "narrative_provenance": "model_assisted",
            "narrative_limitation": (
                f"Closing text written by {model_label or 'the configured model'} from the "
                "recorded facts. The facts, receipts, and unresolved list above are not its "
                "to change."
            ),
        }
    )
