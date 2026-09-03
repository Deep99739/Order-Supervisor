"""The five actions are registered capabilities, not free-form strings.

These are the argument rules the gate relies on. They run without Temporal, Postgres, or
a provider, because "is this a usable request for this particular action" is a question
about the proposal alone.
"""

import pytest

from app.contracts.decision import ActionProposal
from app.domain import actions as registry
from app.domain.vocabulary import ActionAudience, ActionName


def proposal(action: ActionName, **fields) -> ActionProposal:
    values = {
        "action": action,
        "content": "Please confirm the current status for this order.",
        "rationale": "The concern is open and needs an owner.",
    } | fields
    return ActionProposal.model_validate(values)


def test_every_assignment_action_is_registered_with_its_own_audience():
    assert set(registry.REGISTRY) == set(ActionName)
    audiences = {definition.audience for definition in registry.REGISTRY.values()}
    assert len(audiences) == len(ActionName), "each action addresses a distinct audience"
    assert registry.audience_of(ActionName.MESSAGE_CUSTOMER) == ActionAudience.CUSTOMER


@pytest.mark.parametrize(
    "action",
    [
        ActionName.MESSAGE_FULFILLMENT_TEAM,
        ActionName.MESSAGE_PAYMENTS_TEAM,
        ActionName.MESSAGE_LOGISTICS_TEAM,
    ],
)
def test_a_team_message_needs_a_subject_and_the_concern_it_is_about(action):
    assert registry.invalid_arguments(proposal(action, subject="Delay", issue_id="x")) is None
    assert "subject" in registry.invalid_arguments(proposal(action, issue_id="x"))
    assert "concern" in registry.invalid_arguments(proposal(action, subject="Delay"))


def test_a_customer_message_carries_a_body_and_no_subject_line():
    assert registry.invalid_arguments(proposal(ActionName.MESSAGE_CUSTOMER, issue_id="x")) is None
    refused = registry.invalid_arguments(
        proposal(ActionName.MESSAGE_CUSTOMER, issue_id="x", subject="Update")
    )
    assert "does not take a subject" in refused


def test_an_internal_note_needs_a_category_and_may_stand_alone():
    note = proposal(ActionName.CREATE_INTERNAL_NOTE, category="escalation")
    assert registry.invalid_arguments(note) is None, "a note need not name an issue"
    assert "category" in registry.invalid_arguments(proposal(ActionName.CREATE_INTERNAL_NOTE))


def test_a_category_cannot_be_smuggled_onto_a_message():
    refused = registry.invalid_arguments(
        proposal(ActionName.MESSAGE_CUSTOMER, issue_id="x", category="observation")
    )
    assert "does not take a category" in refused


def test_a_receipt_records_the_audience_and_never_claims_a_real_effect():
    details = registry.receipt_details(
        proposal(ActionName.MESSAGE_LOGISTICS_TEAM, subject="Delay", issue_id="shipment-delay"),
        review="not_required",
    )
    assert details["audience"] == str(ActionAudience.LOGISTICS_TEAM)
    assert details["simulated"] is True and details["executed"] is True
