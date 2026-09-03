"""Deadlines and closure rules — the parts the workflow owns outright.

The agent proposes when to look again; this module decides what actually happens. An
unusable proposal falls back to the template default and says so in the record, and no
review is ever scheduled past the order's original age deadline.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.contracts.decision import DecisionProposal
from app.contracts.run import RunSnapshot
from app.domain import guidance
from app.domain.policy import effective_policy
from app.domain.vocabulary import CloseReason


@dataclass(frozen=True)
class WakeSchedule:
    deadline: datetime
    used_default: bool
    explanation: str


def maximum_age_reached(snapshot: RunSnapshot, now: datetime) -> bool:
    return now >= snapshot.maximum_age_at


def closure_cause(snapshot: RunSnapshot, now: datetime) -> CloseReason | None:
    """The only cause this module can observe on its own; the others arrive as commands."""
    return CloseReason.MAXIMUM_AGE_REACHED if maximum_age_reached(snapshot, now) else None


def effective_wake(
    proposal: DecisionProposal | None, snapshot: RunSnapshot, *, now: datetime
) -> WakeSchedule:
    """Validate a proposed sleep against the template's permitted range and the age deadline.

    "Prioritize speed over cost" is one of the assignment's named instructions, and here
    it is a deterministic effect rather than a hint in a prompt: the permitted horizon
    shortens to the template's own default, and an unusable proposal falls back to half
    of it. Both stay inside the configured bounds; speed never invents a new range.
    """
    profile = snapshot.supervisor.wake_profile
    urgent = effective_policy(snapshot).prioritize_speed
    ceiling = profile.default_seconds if urgent else profile.maximum_seconds
    fallback = (
        max(profile.minimum_seconds, profile.default_seconds // 2)
        if urgent
        else profile.default_seconds
    )
    # The agent may ask for a shorter horizon while a concern is open. It can only bring
    # the review forward, never push it out, and it stays inside the template's bounds.
    shortened = guidance.shortened_review(snapshot, now=now)
    if shortened is not None:
        ceiling = min(ceiling, shortened)
        fallback = min(fallback, max(shortened, profile.minimum_seconds))
    requested: float | None = None
    described = "no timing was proposed"

    if proposal is not None:
        if proposal.sleep_for_seconds is not None:
            requested = float(proposal.sleep_for_seconds)
            described = f"{proposal.sleep_for_seconds}s"
        elif proposal.sleep_until is not None:
            requested = (proposal.sleep_until - now).total_seconds()
            described = proposal.sleep_until.isoformat()

    permitted = requested is not None and profile.minimum_seconds <= requested <= ceiling
    seconds = requested if permitted else float(fallback)
    deadline = now + timedelta(seconds=seconds)

    if permitted:
        explanation = f"Next review in {int(seconds)}s."
    else:
        # A malformed, past, or out-of-range request never becomes a hot loop.
        explanation = (
            f"Requested {described} is outside the permitted "
            f"{profile.minimum_seconds}-{ceiling}s range; using {int(fallback)}s."
        )
        if urgent:
            explanation = f"{explanation} A standing instruction prioritises speed."

    if deadline > snapshot.maximum_age_at:
        deadline = snapshot.maximum_age_at
        explanation = f"{explanation} Shortened to the order's original age deadline."

    return WakeSchedule(deadline=deadline, used_default=not permitted, explanation=explanation)
