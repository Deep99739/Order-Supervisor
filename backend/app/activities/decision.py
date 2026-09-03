"""The decision boundary.

The workflow owns timing, staleness, and authority; this activity owns only "what would
be useful here". It builds the context, asks one model once, and validates the answer.
It never writes to the database and never decides whether a proposal is permitted.

Failures are kept distinguishable, because they call for different things from an
operator: no configuration, a provider that would not answer, and a provider that
answered with something unusable are three different problems. In every case the run's
recorded facts, memory, and instructions survive untouched.
"""

from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.agent.prompt import INVARIANTS, decision_prompt
from app.agent.providers import ProviderError, build_provider, parse_json
from app.agent.schema import proposal_schema, to_openapi
from app.config import Settings
from app.contracts.decision import (
    ActionProposal,
    DecisionProposal,
    DecisionRequest,
    DecisionResult,
    MemoryRefresh,
    ProviderUsage,
)
from app.contracts.run import WakeHint
from app.domain import events as event_rules
from app.domain import memory
from app.domain.policy import effective_policy
from app.domain.vocabulary import ActionName

# Which open concern each team is the right audience for.
ISSUE_ACTIONS: tuple[tuple[str, ActionName, str, str], ...] = (
    (
        event_rules.PAYMENT_ISSUE,
        ActionName.MESSAGE_PAYMENTS_TEAM,
        "Payment problem on this order",
        "Please confirm what happened to this payment and whether it can be retried.",
    ),
    (
        event_rules.SHIPMENT_DELAY_ISSUE,
        ActionName.MESSAGE_LOGISTICS_TEAM,
        "Delayed shipment on this order",
        "Please confirm a revised delivery estimate for this shipment.",
    ),
    (
        event_rules.STALLED_ISSUE,
        ActionName.MESSAGE_FULFILLMENT_TEAM,
        "No progress on this order",
        "This order has not progressed. Please confirm what the next step is.",
    ),
)


class DecisionActivities:
    def __init__(self, settings: Settings):
        self.settings = settings

    @activity.defn(name="decide")
    async def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        parsed = DecisionRequest.model_validate(request)
        if self.settings.agent_mode == "scripted":
            # A deterministic stand-in, labelled as one wherever it is recorded.
            return DecisionResult(
                proposal=_scripted(parsed), provenance="scripted"
            ).model_dump(mode="json")
        return (await self._model_decision(parsed)).model_dump(mode="json")

    async def _model_decision(self, request: DecisionRequest) -> DecisionResult:
        settings = self.settings
        snapshot = request.snapshot
        profile = snapshot.supervisor.wake_profile
        urgent = effective_policy(snapshot).prioritize_speed
        schema = proposal_schema(
            list(snapshot.supervisor.allowed_actions),
            minimum=profile.minimum_seconds,
            maximum=profile.default_seconds if urgent else profile.maximum_seconds,
            cutoff=request.context.evidence_through_sequence,
            offer_memory=memory.refresh_due(snapshot),
            issue_ids=[issue.issue_id for issue in snapshot.facts.open_issues],
        )

        try:
            provider = build_provider(
                settings.model_provider, settings.model_name, settings.api_keys
            )
        except ProviderError as error:
            raise _failure("ModelNotConfigured", str(error), retryable=False) from None

        if provider.name == "google":
            schema = to_openapi(schema)

        try:
            reply = await provider.complete(
                system=INVARIANTS, user=decision_prompt(request), schema=schema
            )
        except ProviderError as error:
            raise _failure(
                "ProviderUnavailable" if error.retryable else "ProviderRejected",
                str(error),
                retryable=False,
            ) from None

        try:
            proposal = DecisionProposal.model_validate(
                _clean(parse_json(reply.text), request)
            )
        except ProviderError as error:
            raise _failure("MalformedProposal", str(error), retryable=False) from None
        except Exception as error:  # noqa: BLE001 - reported to the operator as-is
            raise _failure(
                "MalformedProposal",
                f"The answer did not satisfy the proposal contract: {error}",
                retryable=False,
            ) from None

        activity.logger.info(
            "decision %s answered by %s (attempt %s, transport %s)",
            request.decision_id,
            provider.label,
            request.attempt,
            reply.transport_attempts,
        )
        return DecisionResult(
            proposal=proposal,
            provenance="model",
            model_label=provider.label,
            usage=ProviderUsage(
                input_tokens=reply.usage.get("input_tokens"),
                output_tokens=reply.usage.get("output_tokens"),
                transport_attempts=reply.transport_attempts,
            ),
        )


def _failure(kind: str, message: str, *, retryable: bool) -> ApplicationError:
    # The episode owns the retry budget, so nothing here asks the SDK for another go.
    return ApplicationError(message[:1000], type=kind, non_retryable=not retryable)


def _clean(payload: dict[str, Any], request: DecisionRequest) -> dict[str, Any]:
    """Drop the nulls a strict schema forces a model to emit, and shape what remains.

    Unexpected keys are removed rather than rejected — the proposal contract still
    decides whether what is left is usable. Wake hints arrive as a flat list because the
    version and context stamp are not the model's to assign; they are filled in here from
    the decision's own input, so guidance that outlives its context is caught as stale
    rather than quietly believed.
    """
    known = set(DecisionProposal.model_fields)
    cleaned = {key: value for key, value in payload.items() if key in known and value is not None}
    actions = cleaned.get("actions")
    if isinstance(actions, list):
        fields = set(ActionProposal.model_fields)
        cleaned["actions"] = [
            {key: value for key, value in item.items() if key in fields and value is not None}
            for item in actions
            if isinstance(item, dict)
        ]
    hints = payload.get("wake_hints")
    if isinstance(hints, list) and hints:
        fields = set(WakeHint.model_fields)
        cleaned["wake_guidance"] = {
            "version": 1,
            "context": request.context.model_dump(mode="json"),
            "hints": [
                {key: value for key, value in hint.items() if key in fields and value is not None}
                for hint in hints
                if isinstance(hint, dict)
            ],
        }
    return cleaned


def _scripted(request: DecisionRequest) -> DecisionProposal:
    """A deterministic stand-in decision derived from the run's own recorded facts."""
    snapshot = request.snapshot
    facts = snapshot.facts
    allowed = set(snapshot.supervisor.allowed_actions)
    policy = effective_policy(snapshot)
    proposals: list[ActionProposal] = []

    for issue_id, action, subject, content in ISSUE_ACTIONS:
        if len(proposals) >= 5:
            break
        if event_rules.has_issue(facts, issue_id) and action in allowed:
            proposals.append(
                ActionProposal(
                    action=action,
                    subject=subject,
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
                category="escalation",
                content=f"These concerns need a person to look at them: {names}.",
                issue_id=flagged[0].issue_id,
                rationale="Flagged evidence cannot be resolved without human judgement.",
            )
        )

    profile = snapshot.supervisor.wake_profile
    if facts.open_issues or policy.prioritize_speed:
        # Look again sooner while something is unresolved.
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

    refresh = None
    if memory.refresh_due(snapshot):
        # The stand-in refreshes deterministically, and declares the cutoff it can
        # honestly claim: everything recorded so far.
        refresh = MemoryRefresh(
            text=memory.render_summary(snapshot), through_sequence=snapshot.last_sequence
        )

    return DecisionProposal(
        rationale=rationale[:2000],
        actions=proposals,
        sleep_for_seconds=seconds,
        memory_refresh=refresh,
    )
