"""The decision boundary.

The workflow owns timing, staleness, and authority; this activity owns only "what would
be useful here". Today it answers from a small deterministic script so the orchestration
around it can be exercised honestly. **A scripted answer is not an AI demonstration**,
and it is labelled as such in every record it produces.

The real provider adapter replaces `_scripted` behind this same contract.
"""

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.config import Settings
from app.contracts.decision import ActionProposal, DecisionProposal, DecisionRequest, DecisionResult
from app.contracts.run import RunSnapshot
from app.domain import events as event_rules
from app.domain.policy import effective_policy
from app.domain.vocabulary import ActionName

# Which open concern each team is the right audience for.
ISSUE_ACTIONS: tuple[tuple[str, ActionName, str], ...] = (
    (
        event_rules.PAYMENT_ISSUE,
        ActionName.MESSAGE_PAYMENTS_TEAM,
        "Ask the payments team to confirm what happened to this payment.",
    ),
    (
        event_rules.SHIPMENT_DELAY_ISSUE,
        ActionName.MESSAGE_LOGISTICS_TEAM,
        "Ask logistics for a revised delivery estimate for this shipment.",
    ),
    (
        event_rules.STALLED_ISSUE,
        ActionName.MESSAGE_FULFILLMENT_TEAM,
        "Ask fulfillment why this order has not progressed.",
    ),
)


class DecisionActivities:
    def __init__(self, settings: Settings):
        self.settings = settings

    @activity.defn(name="decide")
    async def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        parsed = DecisionRequest.model_validate(request)
        if self.settings.agent_mode != "scripted":
            # No provider adapter exists yet. Saying so is better than quietly pretending,
            # and the workflow turns this into a visible recovery state.
            raise ApplicationError(
                "The model adapter is not implemented yet. Set AGENT_MODE=scripted to "
                "exercise the supervisor lifecycle with deterministic decisions.",
                type="ModelAdapterMissing",
                non_retryable=True,
            )
        return DecisionResult(
            proposal=_scripted(parsed.snapshot), provenance="scripted"
        ).model_dump(mode="json")


def _scripted(snapshot: RunSnapshot) -> DecisionProposal:
    """A deterministic stand-in decision derived from the run's own recorded facts."""
    facts = snapshot.facts
    allowed = set(snapshot.supervisor.allowed_actions)
    policy = effective_policy(snapshot)
    proposals: list[ActionProposal] = []

    for issue_id, action, content in ISSUE_ACTIONS:
        if len(proposals) >= 5:
            break
        if event_rules.has_issue(facts, issue_id) and action in allowed:
            proposals.append(
                ActionProposal(
                    action=action,
                    content=content,
                    issue_id=issue_id,
                    rationale=f"{issue_id} is open and needs an owner.",
                )
            )

    flagged = [issue for issue in facts.open_issues if issue.review_required]
    if flagged and ActionName.CREATE_INTERNAL_NOTE in allowed and len(proposals) < 5:
        names = ", ".join(issue.issue_id for issue in flagged)
        proposals.append(
            ActionProposal(
                action=ActionName.CREATE_INTERNAL_NOTE,
                content=f"These concerns need a person to look at them: {names}.",
                issue_id=flagged[0].issue_id,
                rationale="Flagged evidence cannot be resolved without human judgement.",
            )
        )

    profile = snapshot.supervisor.wake_profile
    if facts.open_issues:
        # Look again sooner while something is unresolved.
        seconds = max(profile.minimum_seconds, profile.default_seconds // 2)
    elif policy.prioritize_speed:
        seconds = max(profile.minimum_seconds, profile.default_seconds // 2)
    else:
        seconds = profile.default_seconds

    if facts.open_issues:
        rationale = (
            f"{len(facts.open_issues)} concern(s) are open: "
            f"{', '.join(issue.issue_id for issue in facts.open_issues)}. "
            f"Following up and reviewing again in {seconds}s."
        )
    else:
        rationale = (
            f"Payment is {facts.payment} and shipment is {facts.shipment}, with nothing "
            f"unresolved. No action is useful yet; reviewing again in {seconds}s."
        )

    return DecisionProposal(
        rationale=rationale[:2000],
        actions=proposals,
        sleep_for_seconds=seconds,
    )
