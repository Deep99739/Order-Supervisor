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

PolicyOutcome = Literal["wake_now", "defer", "review_required"]


@dataclass(frozen=True)
class EffectivePolicy:
    """The frozen template's defaults, overridden by explicit operator instructions."""

    prioritize_speed: bool
    escalate_shipment_delays: bool
    require_customer_review: bool


@dataclass(frozen=True)
class PolicyDecision:
    outcome: PolicyOutcome
    # The review flag and the inference trigger are separate: an unknown event can both
    # raise a flag for a person and ask the agent to look at it.
    wake: bool
    review_required: bool
    reason: str
    # Filled once the agent may propose guidance; the policy records which version it used.
    guidance_version: int | None = None


def effective_policy(snapshot: RunSnapshot) -> EffectivePolicy:
    template = snapshot.supervisor
    resolved = EffectivePolicy(
        prioritize_speed=template.prioritize_speed,
        escalate_shipment_delays=template.escalate_shipment_delays,
        require_customer_review=template.customer_review_default,
    )
    for instruction in snapshot.instructions:
        changes = instruction.policy_changes
        if changes is None:
            continue
        resolved = EffectivePolicy(
            prioritize_speed=(
                resolved.prioritize_speed
                if changes.prioritize_speed is None
                else changes.prioritize_speed
            ),
            escalate_shipment_delays=(
                resolved.escalate_shipment_delays
                if changes.escalate_shipment_delays is None
                else changes.escalate_shipment_delays
            ),
            require_customer_review=(
                resolved.require_customer_review
                if changes.require_customer_review is None
                else changes.require_customer_review
            ),
        )
    return resolved


def classify(
    outcome: EventOutcome,
    snapshot: RunSnapshot,
    event_type: str,
    *,
    guidance_version: int | None = None,
) -> PolicyDecision:
    """Decide whether this admitted event should wake the main agent now."""
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

    if outcome.importance == "important":
        return PolicyDecision(
            "wake_now",
            wake=True,
            review_required=False,
            reason=outcome.explanation,
            guidance_version=guidance_version,
        )

    return PolicyDecision(
        "defer",
        wake=False,
        review_required=False,
        reason=f"{outcome.explanation} The next scheduled review still stands.",
        guidance_version=guidance_version,
    )
