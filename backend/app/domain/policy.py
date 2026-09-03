"""The lightweight wake policy.

This is a small deterministic classifier, not a model call. It decides whether an event
is worth waking the main agent for right now. "Do not wake" means exactly that — the
event is still admitted, interpreted, and recorded; only the inference is deferred.

Operator controls and the unknown-event rule sit above anything the agent could later
suggest, so generated guidance can never suppress them.
"""

from dataclasses import dataclass
from typing import Literal

from app.contracts.run import RunSnapshot
from app.domain.events import EventOutcome
from app.domain.vocabulary import KnownEvent

# These are also the dispositions the policy record carries, so the classifier's verdict
# and what the timeline shows can never drift apart.
PolicyOutcome = Literal["wake_now", "deferred", "review_required"]


@dataclass(frozen=True)
class EffectivePolicy:
    """The frozen template's defaults, overridden by explicit operator instructions."""

    prioritize_speed: bool
    escalate_shipment_delays: bool
    require_customer_review: bool
    # True when review is on because free text arrived whose customer-contact stance is
    # unclear, rather than because anyone asked for it. The UI says which it is.
    review_from_ambiguity: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    # The review flag and the inference trigger are separate: an unknown event can both
    # raise a flag for a person and ask the agent to look at it.
    wake: bool
    review_required: bool
    reason: str
    # Which guidance, if any, this outcome depended on. Recorded either way, so a later
    # reader can tell an agent-influenced wake from a template one.
    guidance_version: int | None = None
    guidance_hint: str | None = None


def effective_policy(snapshot: RunSnapshot) -> EffectivePolicy:
    """Merge the frozen template with the named controls the operator actually set.

    Free-form instruction text is pinned guidance for the agent; this system does not
    claim to compile arbitrary English into policy. So when an instruction arrives whose
    stance on customer contact is unstated, customer messages fall back to requiring
    approval until the operator answers that question through the named control. The
    conservative direction is a review hold, never inferred permission.
    """
    template = snapshot.supervisor
    prioritize_speed = template.prioritize_speed
    escalate_delays = template.escalate_shipment_delays
    review = template.customer_review_default
    review_stated = False
    unclassified = False

    for instruction in snapshot.instructions:
        changes = instruction.policy_changes
        if changes is None:
            unclassified = True
            continue
        if changes.prioritize_speed is not None:
            prioritize_speed = changes.prioritize_speed
        if changes.escalate_shipment_delays is not None:
            escalate_delays = changes.escalate_shipment_delays
        if changes.require_customer_review is None:
            unclassified = True
        else:
            review = changes.require_customer_review
            review_stated = True

    ambiguous = unclassified and not review_stated and not review
    return EffectivePolicy(
        prioritize_speed=prioritize_speed,
        escalate_shipment_delays=escalate_delays,
        require_customer_review=review or ambiguous,
        review_from_ambiguity=ambiguous,
    )


def classify(
    outcome: EventOutcome,
    snapshot: RunSnapshot,
    event_type: str,
    *,
    hints: tuple = (),
    guidance_version: int | None = None,
) -> PolicyDecision:
    """Decide whether this admitted event should wake the main agent now.

    The order below is the safety property. Terminal evidence, an unresolvable payload,
    and an operator's standing escalation are settled before any generated guidance is
    consulted, so a hint can only influence what was still genuinely open to judgement.
    """
    # 1. Rules that are not open to influence.
    if outcome.terminal:
        return PolicyDecision(
            "wake_now",
            wake=False,
            review_required=False,
            reason="Terminal order evidence; the workflow closes without asking the agent.",
            guidance_version=guidance_version,
        )

    if outcome.review_required:
        return PolicyDecision(
            "review_required",
            wake=True,
            review_required=True,
            reason=outcome.explanation,
            guidance_version=guidance_version,
        )

    policy = effective_policy(snapshot)
    if event_type == KnownEvent.SHIPMENT_DELAYED and policy.escalate_shipment_delays:
        return PolicyDecision(
            "wake_now",
            wake=True,
            review_required=False,
            reason="A standing instruction escalates shipment delays immediately.",
            guidance_version=guidance_version,
        )

    # 2. Only now does what the agent asked for come into it.
    watching = _hint(hints, "watch_for_progress", event_type)
    if watching is not None:
        return PolicyDecision(
            "wake_now",
            wake=True,
            review_required=False,
            reason=(
                f"{outcome.explanation} The agent asked to be woken by {event_type} while "
                f"{watching.issue_id} is open."
            ),
            guidance_version=guidance_version,
            guidance_hint=watching.kind,
        )

    settled = not outcome.facts.open_issues
    deferring = _hint(hints, "defer_routine", event_type) if settled else None
    if deferring is not None:
        return PolicyDecision(
            "deferred",
            wake=False,
            review_required=False,
            reason=(
                f"{outcome.explanation} The agent asked for routine {event_type} to be "
                "recorded without a review while nothing is unresolved."
            ),
            guidance_version=guidance_version,
            guidance_hint=deferring.kind,
        )

    if outcome.importance == "important":
        return PolicyDecision(
            "wake_now",
            wake=True,
            review_required=False,
            reason=outcome.explanation,
            guidance_version=guidance_version,
        )

    return PolicyDecision(
        "deferred",
        wake=False,
        review_required=False,
        reason=f"{outcome.explanation} The next scheduled review still stands.",
        guidance_version=guidance_version,
    )


def _hint(hints: tuple, kind: str, event_type: str):
    for hint in hints:
        if hint.kind == kind and hint.event_type == event_type:
            return hint
    return None
