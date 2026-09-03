"""What the agent is allowed to teach the classifier.

The wake policy stays a small deterministic thing that a person can read. Guidance does
not change that — it supplies a few typed, expiring parameters the policy consults *after*
it has already applied every rule that is not negotiable. That ordering is the whole
safety story: a hint can bring a review forward or let routine progress pass quietly, and
it can never grant permission, silence a terminal event, or talk its way past an operator.

Guidance is also perishable. It carries the context it was written for, names the concern
it serves, and expires. When the concern is settled or the situation moves, the hint stops
applying without anyone having to remember to withdraw it.
"""

from dataclasses import dataclass
from datetime import datetime

from app.contracts.run import ContextStamp, OpenIssue, RunSnapshot, WakeGuidance, WakeHint
from app.domain.policy import effective_policy
from app.domain.vocabulary import GUIDANCE_HINTS, KnownEvent

# Progress that may be recorded without waking the agent. Deliberately short: a failure,
# a delay, a refund, a customer question, or an unfamiliar type is never "routine".
DEFERRABLE_EVENTS = frozenset({KnownEvent.SHIPMENT_CREATED, KnownEvent.PAYMENT_CONFIRMED})

# Events an agent must never be able to arrange to sleep through.
MANDATORY_EVENTS = frozenset({KnownEvent.DELIVERED, KnownEvent.SHIPMENT_DELAYED})


@dataclass(frozen=True)
class Refusal:
    hint: WakeHint
    reason: str
    explanation: str


@dataclass(frozen=True)
class Review:
    accepted: tuple[WakeHint, ...] = ()
    refused: tuple[Refusal, ...] = ()

    @property
    def usable(self) -> bool:
        return bool(self.accepted)


def _open_issue(snapshot: RunSnapshot, issue_id: str | None) -> OpenIssue | None:
    for issue in snapshot.facts.open_issues:
        if issue.issue_id == issue_id:
            return issue
    return None


def _refuse(hint: WakeHint, reason: str, explanation: str) -> Refusal:
    return Refusal(hint=hint, reason=reason, explanation=explanation)


def check(
    guidance: WakeGuidance, snapshot: RunSnapshot, stamp: ContextStamp, *, now: datetime
) -> Review:
    """Validate proposed guidance against the situation it claims to be about.

    Whatever is refused here is refused individually and on the record. A bad hint never
    costs the run its existing next deadline, and it never removes a good sibling.
    """
    if guidance.context.context_version != stamp.context_version:
        return Review(
            refused=tuple(
                _refuse(
                    hint,
                    "stale_context",
                    "The guidance describes a context this decision no longer matches.",
                )
                for hint in guidance.hints
            )
        )

    policy = effective_policy(snapshot)
    profile = snapshot.supervisor.wake_profile
    accepted: list[WakeHint] = []
    refused: list[Refusal] = []

    for hint in guidance.hints[:GUIDANCE_HINTS]:
        if hint.expires_at <= now:
            refused.append(_refuse(hint, "expired", "The hint expired before it was adopted."))
            continue
        if hint.expires_at > snapshot.maximum_age_at:
            refused.append(
                _refuse(
                    hint,
                    "outlives_the_order",
                    "A hint cannot outlast the order's own maximum age.",
                )
            )
            continue
        if hint.issue_id is not None and _open_issue(snapshot, hint.issue_id) is None:
            refused.append(
                _refuse(
                    hint,
                    "unknown_issue",
                    f"No open concern called {hint.issue_id} exists for this order.",
                )
            )
            continue
        if hint.kind == "shorten_review" and not (
            profile.minimum_seconds <= hint.review_after_seconds <= profile.maximum_seconds
        ):
            refused.append(
                _refuse(
                    hint,
                    "interval_out_of_range",
                    f"{hint.review_after_seconds}s is outside the template's permitted "
                    f"{profile.minimum_seconds}-{profile.maximum_seconds}s range.",
                )
            )
            continue
        if hint.kind == "defer_routine":
            if hint.event_type in MANDATORY_EVENTS or hint.event_type not in DEFERRABLE_EVENTS:
                refused.append(
                    _refuse(
                        hint,
                        "not_routine",
                        f"{hint.event_type} is not routine progress and cannot be arranged "
                        "away.",
                    )
                )
                continue
            if policy.escalate_shipment_delays and hint.event_type == KnownEvent.SHIPMENT_DELAYED:
                refused.append(
                    _refuse(
                        hint,
                        "suppresses_a_restriction",
                        "A standing instruction escalates delays; guidance cannot undo it.",
                    )
                )
                continue
        accepted.append(hint)

    return Review(accepted=tuple(accepted), refused=tuple(refused))


def active(snapshot: RunSnapshot, *, now: datetime) -> tuple[WakeHint, ...]:
    """The hints that still apply right now.

    Reconsidered rather than remembered: a hint whose concern was settled, or whose
    expiry has passed, simply stops counting. Nothing has to withdraw it.
    """
    guidance = snapshot.wake_guidance
    if guidance is None:
        return ()
    if guidance.context.control_epoch != snapshot.control_epoch:
        # An operator boundary moved; guidance written before it no longer speaks.
        return ()
    return tuple(
        hint
        for hint in guidance.hints
        if hint.expires_at > now
        and (hint.issue_id is None or _open_issue(snapshot, hint.issue_id) is not None)
    )


def watching(hints: tuple[WakeHint, ...], event_type: str) -> WakeHint | None:
    """A hint asking to be woken when this kind of event arrives."""
    for hint in hints:
        if hint.kind == "watch_for_progress" and hint.event_type == event_type:
            return hint
    return None


def deferring(hints: tuple[WakeHint, ...], event_type: str) -> WakeHint | None:
    """A hint asking for this routine progress to be recorded without a review."""
    for hint in hints:
        if hint.kind == "defer_routine" and hint.event_type == event_type:
            return hint
    return None


def shortened_review(snapshot: RunSnapshot, *, now: datetime) -> int | None:
    """The earliest review interval a still-valid hint asks for, if any."""
    intervals = [
        hint.review_after_seconds
        for hint in active(snapshot, now=now)
        if hint.kind == "shorten_review"
    ]
    return min(intervals) if intervals else None
