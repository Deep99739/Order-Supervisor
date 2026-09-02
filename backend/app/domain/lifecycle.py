"""Deadlines and closure rules — the parts the workflow owns outright.

The agent proposes when to look again; this module decides what actually happens. An
unusable proposal falls back to the template default and says so in the record, and no
review is ever scheduled past the order's original age deadline.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.contracts.decision import DecisionProposal
from app.contracts.run import RunSnapshot
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
    """Validate a proposed sleep against the template's permitted range and the age deadline."""
    profile = snapshot.supervisor.wake_profile
    requested: float | None = None
    described = "no timing was proposed"

    if proposal is not None:
        if proposal.sleep_for_seconds is not None:
            requested = float(proposal.sleep_for_seconds)
            described = f"{proposal.sleep_for_seconds}s"
        elif proposal.sleep_until is not None:
            requested = (proposal.sleep_until - now).total_seconds()
            described = proposal.sleep_until.isoformat()

    permitted = requested is not None and (
        profile.minimum_seconds <= requested <= profile.maximum_seconds
    )
    seconds = requested if permitted else float(profile.default_seconds)
    deadline = now + timedelta(seconds=seconds)

    if permitted:
        explanation = f"Next review in {int(seconds)}s."
    else:
        # A malformed, past, or out-of-range request never becomes a hot loop.
        explanation = (
            f"Requested {described} is outside the permitted "
            f"{profile.minimum_seconds}-{profile.maximum_seconds}s range; "
            f"using the {profile.default_seconds}s default."
        )

    if deadline > snapshot.maximum_age_at:
        deadline = snapshot.maximum_age_at
        explanation = f"{explanation} Shortened to the order's original age deadline."

    return WakeSchedule(deadline=deadline, used_default=not permitted, explanation=explanation)
