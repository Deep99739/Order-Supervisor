"""The five business actions, registered explicitly.

Every effect this system can have is one of these five records. The registry is what
turns a model's `action` string into something with an audience, required arguments, and
a receipt shape — so a fluent sentence claiming "I notified logistics" is not an action,
and a proposal naming an unregistered capability has nowhere to go.

Audience is supplied here, never by the model. No email, SMS, payment, refund, carrier,
or helpdesk call is made: a successful effect is a recorded activity row.
"""

from dataclasses import dataclass
from typing import Any

from app.contracts.decision import ActionProposal
from app.domain.vocabulary import ActionAudience, ActionName, NoteCategory


@dataclass(frozen=True)
class ActionDefinition:
    name: ActionName
    audience: ActionAudience
    # Team messages carry a subject line; a customer message and an internal note do not.
    requires_subject: bool
    # A contact needs the concern it is about, which is also how repeated contact about
    # unchanged work is detected. An internal note may stand on its own.
    requires_issue: bool
    requires_category: bool
    # Only customer contact can be gated behind human approval.
    reviewable: bool
    summary: str
    guidance: str


REGISTRY: dict[ActionName, ActionDefinition] = {
    ActionName.MESSAGE_FULFILLMENT_TEAM: ActionDefinition(
        name=ActionName.MESSAGE_FULFILLMENT_TEAM,
        audience=ActionAudience.FULFILLMENT_TEAM,
        requires_subject=True,
        requires_issue=True,
        requires_category=False,
        reviewable=False,
        summary="Record a message to the fulfillment team about this order.",
        guidance="Use when the order has not progressed and fulfillment owns the next step.",
    ),
    ActionName.MESSAGE_PAYMENTS_TEAM: ActionDefinition(
        name=ActionName.MESSAGE_PAYMENTS_TEAM,
        audience=ActionAudience.PAYMENTS_TEAM,
        requires_subject=True,
        requires_issue=True,
        requires_category=False,
        reviewable=False,
        summary="Record a message to the payments team about this order.",
        guidance=(
            "Use to ask about a payment problem. Asking the payments team to look into a "
            "failure is not the same as payment being confirmed."
        ),
    ),
    ActionName.MESSAGE_LOGISTICS_TEAM: ActionDefinition(
        name=ActionName.MESSAGE_LOGISTICS_TEAM,
        audience=ActionAudience.LOGISTICS_TEAM,
        requires_subject=True,
        requires_issue=True,
        requires_category=False,
        reviewable=False,
        summary="Record a message to the logistics team about this order.",
        guidance="Use for a shipment delay or a missing carrier update.",
    ),
    ActionName.MESSAGE_CUSTOMER: ActionDefinition(
        name=ActionName.MESSAGE_CUSTOMER,
        audience=ActionAudience.CUSTOMER,
        requires_subject=False,
        requires_issue=True,
        requires_category=False,
        reviewable=True,
        summary="Record a message to the customer.",
        guidance=(
            "Use only when the customer needs information they do not already have. "
            "Recording a message is not sending one, and it may require human approval."
        ),
    ),
    ActionName.CREATE_INTERNAL_NOTE: ActionDefinition(
        name=ActionName.CREATE_INTERNAL_NOTE,
        audience=ActionAudience.INTERNAL,
        requires_subject=False,
        requires_issue=False,
        requires_category=True,
        reviewable=False,
        summary="Record an internal note for whoever reviews this order next.",
        guidance=(
            "Use to leave an observation, escalation, or recommendation. This is the right "
            "place for something a person must judge."
        ),
    ),
}


def definition(action: ActionName) -> ActionDefinition:
    return REGISTRY[action]


def audience_of(action: ActionName) -> ActionAudience:
    return REGISTRY[action].audience


def invalid_arguments(proposal: ActionProposal) -> str | None:
    """Return why this action's arguments are unusable, or None when they are fine.

    Length and type are already settled by the DTO. What is checked here is whether the
    arguments make sense *for this particular action*.
    """
    registered = REGISTRY[proposal.action]
    if registered.requires_subject and not proposal.subject:
        return f"{proposal.action} needs a short subject line."
    if not registered.requires_subject and proposal.subject:
        return f"{proposal.action} does not take a subject line."
    if registered.requires_category and proposal.category is None:
        return (
            f"{proposal.action} needs a category of "
            f"{', '.join(str(item) for item in NoteCategory)}."
        )
    if not registered.requires_category and proposal.category is not None:
        return f"{proposal.action} does not take a category."
    if registered.requires_issue and not proposal.issue_id:
        return f"{proposal.action} must name the open concern it is about."
    return None


def receipt_details(proposal: ActionProposal, *, review: str) -> dict[str, Any]:
    """The recorded body of a committed action. `simulated` is not decoration: it is the
    difference between this record and a real message."""
    registered = REGISTRY[proposal.action]
    return {
        "action": str(proposal.action),
        "audience": str(registered.audience),
        "subject": proposal.subject,
        "category": str(proposal.category) if proposal.category else None,
        "content": proposal.content,
        "issue_id": proposal.issue_id,
        "rationale": proposal.rationale,
        "review": review,
        "executed": True,
        "simulated": True,
    }
