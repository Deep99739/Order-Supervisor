"""Authorize a decision against the context that is true *now*, not when it was made.

A model result is a proposal. Between the moment it was requested and the moment its
effects would land, an operator can pause the run, a terminal event can arrive, an
instruction can change what is permitted, and the order's facts can move. This module is
the gate between the two, and it is deliberately pure so the rules can be read and tested
without Temporal or a database.

Three things are worth knowing before changing anything here:

* **A global failure blocks everything; a local failure blocks one proposal.** Valid
  internal work still commits when a sibling customer message only needs approval.
* **Blocked is not failed.** Each refusal keeps its own reason, because "not permitted",
  "already asked them yesterday", and "needs a person to approve it" are different
  operator decisions.
* **Nothing here executes anything.** It returns what *may* be committed. The receipt
  from the write boundary is what makes an effect real.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.contracts.decision import ActionProposal, DecisionProposal
from app.contracts.run import ContextStamp, OpenIssue, RunSnapshot
from app.domain import actions as registry
from app.domain.policy import effective_policy
from app.domain.vocabulary import (
    FOLLOW_UP_INTERVALS,
    ActionAudience,
    ActionName,
    BlockReason,
    action_id,
)


@dataclass(frozen=True)
class AdmittedAction:
    ordinal: int
    action_id: str
    audience: ActionAudience
    proposal: ActionProposal
    # "approved" when a human signed off on this exact content, "not_required" otherwise.
    review: str

    @property
    def action(self) -> ActionName:
        return self.proposal.action


@dataclass(frozen=True)
class BlockedAction:
    ordinal: int
    action_id: str
    action: ActionName
    reason: BlockReason
    explanation: str


@dataclass(frozen=True)
class DraftRequest:
    """A permitted customer message that a person must approve before it can happen."""

    ordinal: int
    action_id: str
    proposal: ActionProposal
    reason: str


@dataclass(frozen=True)
class Authorization:
    stale: bool
    global_block: BlockReason | None
    explanation: str
    admitted: tuple[AdmittedAction, ...] = ()
    blocked: tuple[BlockedAction, ...] = ()
    draft: DraftRequest | None = None

    @property
    def commits_anything(self) -> bool:
        return bool(self.admitted) or self.draft is not None


def follow_up_interval(snapshot: RunSnapshot) -> timedelta:
    """How long an unchanged issue waits before the same audience may be written to again.

    This is a configured policy value derived from the template's own review rhythm, not
    permission the model can grant itself by proposing the message again.
    """
    return timedelta(seconds=snapshot.supervisor.wake_profile.default_seconds * FOLLOW_UP_INTERVALS)


def _issue(snapshot: RunSnapshot, issue_id: str | None) -> OpenIssue | None:
    if issue_id is None:
        return None
    for issue in snapshot.facts.open_issues:
        if issue.issue_id == issue_id:
            return issue
    return None


def _repeated_contact(
    issue: OpenIssue, audience: ActionAudience, snapshot: RunSnapshot, now: datetime
) -> str | None:
    """Block a second contact about work that has not changed since the first one."""
    for contact in issue.contacts:
        if contact.audience != audience:
            continue
        if snapshot.context_version > contact.context_version:
            return None  # Something material happened; there is genuinely more to say.
        if now >= contact.follow_up_at:
            return None  # The follow-up window elapsed.
        return (
            f"{audience} was already contacted about {issue.issue_id} as {contact.action_id}, "
            f"nothing has changed since, and follow-up is not due until "
            f"{contact.follow_up_at.isoformat()}."
        )
    return None


def authorize(
    snapshot: RunSnapshot,
    proposal: DecisionProposal,
    stamp: ContextStamp,
    decision_reference: str,
    *,
    now: datetime,
    closing: bool,
    held: bool,
) -> Authorization:
    """Decide which of a decision's proposals may become effects right now."""
    if closing:
        return Authorization(
            stale=False,
            global_block=BlockReason.RUN_CLOSING,
            explanation="Supervision is closing, so no further business action is authorised.",
        )
    if held:
        return Authorization(
            stale=False,
            global_block=BlockReason.RUN_HELD,
            explanation=(
                "An operator hold applies, so nothing this review proposed is authorised. "
                "The conclusions are recorded, not acted on."
            ),
        )
    if (
        snapshot.context_version != stamp.context_version
        or snapshot.control_epoch != stamp.control_epoch
    ):
        # The decision answered a question about a context that has since moved.
        return Authorization(
            stale=True,
            global_block=BlockReason.STALE_CONTEXT,
            explanation=(
                "The order's context changed while this review was running "
                f"(context {stamp.context_version}->{snapshot.context_version}, "
                f"controls {stamp.control_epoch}->{snapshot.control_epoch}); "
                "its conclusions are discarded rather than applied to a newer situation."
            ),
        )

    policy = effective_policy(snapshot)
    allowed = set(snapshot.supervisor.allowed_actions)
    admitted: list[AdmittedAction] = []
    blocked: list[BlockedAction] = []
    draft: DraftRequest | None = None

    for ordinal, action in enumerate(proposal.actions, start=1):
        reference = action_id(decision_reference, ordinal)
        definition = registry.definition(action.action)

        def refuse(reason: BlockReason, explanation: str) -> None:
            blocked.append(
                BlockedAction(
                    ordinal=ordinal,
                    action_id=reference,
                    action=action.action,
                    reason=reason,
                    explanation=explanation[:500],
                )
            )

        if action.action not in allowed:
            refuse(
                BlockReason.NOT_PERMITTED,
                f"{action.action} is not on this supervisor's permitted actions.",
            )
            continue

        problem = registry.invalid_arguments(action)
        if problem is not None:
            refuse(BlockReason.INVALID_ARGUMENTS, problem)
            continue

        issue = _issue(snapshot, action.issue_id)
        if action.issue_id is not None and issue is None:
            refuse(
                BlockReason.UNKNOWN_ISSUE,
                f"No open concern called {action.issue_id} exists for this order.",
            )
            continue

        if issue is not None:
            repeat = _repeated_contact(issue, definition.audience, snapshot, now)
            if repeat is not None:
                refuse(BlockReason.REPEATED_CONTACT, repeat)
                continue

        if definition.reviewable and policy.require_customer_review:
            if snapshot.pending_review is not None or draft is not None:
                refuse(
                    BlockReason.DRAFT_PENDING,
                    "Another customer draft is already waiting for review; this run holds "
                    "one draft at a time.",
                )
                continue
            # Not blocked, and not sent either: it becomes a draft a person can act on.
            draft = DraftRequest(
                ordinal=ordinal,
                action_id=reference,
                proposal=action,
                reason=(
                    "Free-form instructions were added whose stance on customer contact is "
                    "unstated, so customer messages need approval until an operator says "
                    "otherwise."
                    if policy.review_from_ambiguity
                    else "This run requires human review before the customer is contacted."
                ),
            )
            continue

        admitted.append(
            AdmittedAction(
                ordinal=ordinal,
                action_id=reference,
                audience=definition.audience,
                proposal=action,
                review="not_required",
            )
        )

    return Authorization(
        stale=False,
        global_block=None,
        explanation="Authorised against the current context.",
        admitted=tuple(admitted),
        blocked=tuple(blocked),
        draft=draft,
    )
