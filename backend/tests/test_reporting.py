"""T13 (rules) — what a closing report may say.

Two failures matter here and they pull in opposite directions. A report that says nothing
because generation failed is useless, so the factual renderer has to be a real report:
all four sections, populated from receipts a reader can find in the timeline. And a report
that says something the record contradicts is worse than useless, so a generated narrative
has to be refusable on evidence rather than on taste.

No provider and no database are involved. These are the rules themselves.
"""

from uuid import uuid4

from app.contracts.report import ReportNarrative
from app.contracts.run import CommittedAction, CustomerDraft, RefusedAction
from app.domain import reporting
from app.domain.vocabulary import ActionName, BlockReason, CloseReason
from tests.conftest import RULES_NOW, sample_snapshot


def receipt(
    action: ActionName, sequence: int = 5, content: str = "Please look into this."
) -> CommittedAction:
    return CommittedAction(
        action_id=f"{uuid4()}/action/1",
        action=action,
        content=content,
        receipt={"sequence": sequence, "activity_id": uuid4()},
        recorded_at=RULES_NOW,
    )


def refusal(reason: BlockReason, action: ActionName = ActionName.MESSAGE_CUSTOMER) -> RefusedAction:
    return RefusedAction(
        action=action,
        reason=reason,
        explanation=f"Refused because {reason}.",
        sequence=9,
        recorded_at=RULES_NOW,
    )


def issue(issue_id: str, description: str, **extra) -> dict:
    return {
        "issue_id": issue_id,
        "description": description,
        "evidence": [{"sequence": 3, "activity_id": str(uuid4())}],
        **extra,
    }


def narrative(text: str, **extra) -> ReportNarrative:
    return ReportNarrative(summary=text, **extra)


# ------------------------------------------------------- the factual report stands alone


def test_the_factual_report_is_a_real_report_not_a_placeholder():
    snapshot = sample_snapshot(
        facts={"payment": "confirmed", "shipment": "delivered"},
        counters={"unique_events": 4, "decisions": 3, "deferred_events": 1},
    )
    report = reporting.factual(
        snapshot,
        CloseReason.DELIVERED,
        now=RULES_NOW,
        committed=[receipt(ActionName.MESSAGE_LOGISTICS_TEAM)],
        refused=[],
    )

    assert report.narrative_provenance == "factual_fallback"
    assert report.narrative_limitation, "a fallback says it is one"
    # All four sections the assignment asks for are present without a model.
    assert "Delivery was recorded" in report.summary
    assert report.learnings and report.feedback
    assert len(report.important_actions) == 1
    # And the counts a reader can check against the timeline are actually there.
    assert any("4 order event(s)" in item for item in report.learnings)
    assert any("logistics_team" in item for item in report.learnings)


def test_delivery_does_not_resolve_a_refund_question():
    snapshot = sample_snapshot(
        facts={
            "payment": "confirmed",
            "shipment": "delivered",
            "open_issues": [issue("refund", "Refund requested: arrived damaged")],
        }
    )
    report = reporting.factual(
        snapshot, CloseReason.DELIVERED, now=RULES_NOW, committed=[], refused=[]
    )

    assert [item.issue_id for item in report.unresolved_issues] == ["refund"]
    assert "1 concern(s) remain unresolved" in report.summary
    assert any("human follow-up" in item for item in report.feedback)


def test_refused_proposals_are_reported_apart_from_executed_work():
    snapshot = sample_snapshot()
    report = reporting.factual(
        snapshot,
        CloseReason.MANUALLY_TERMINATED,
        now=RULES_NOW,
        committed=[receipt(ActionName.CREATE_INTERNAL_NOTE)],
        refused=[refusal(BlockReason.APPROVAL_REQUIRED), refusal(BlockReason.REPEATED_CONTACT)],
    )

    assert len(report.important_actions) == 1
    assert len(report.blocked_actions) == 2
    assert all(item.executed is False for item in report.blocked_actions)
    assert any("were not carried out" in item for item in report.learnings)
    assert any("waited on human approval" in item for item in report.feedback)


def test_an_unspent_draft_is_named_in_the_handoff():
    snapshot = sample_snapshot()
    draft = CustomerDraft(
        draft_id=f"{snapshot.run_id}/draft/1",
        decision_id=f"{snapshot.run_id}/decision/1",
        action_id=f"{snapshot.run_id}/decision/1/action/1",
        issue_id="refund",
        content="We are looking into your refund.",
        content_digest="a" * 64,
        reason="This run requires human review before the customer is contacted.",
        context={"context_version": 1, "control_epoch": 0, "evidence_through_sequence": 4},
        status="pending",
    )
    report = reporting.factual(
        snapshot,
        CloseReason.MANUALLY_TERMINATED,
        now=RULES_NOW,
        committed=[],
        refused=[],
        abandoned=draft,
    )

    assert any(draft.draft_id in item for item in report.feedback)
    assert any("never written to" in item for item in report.feedback)


# --------------------------------------------------- a narrative is checked, not trusted


def test_a_narrative_that_matches_the_record_is_accepted():
    snapshot = sample_snapshot(facts={"payment": "confirmed", "shipment": "delivered"})
    text = narrative(
        "The parcel reached the customer after one carrier delay. A message to the "
        "logistics team was recorded asking for a revised estimate.",
        learnings=["The carrier delay needed chasing before it moved."],
        feedback=["Watch this carrier's hub for repeat backlogs."],
    )

    assert reporting.contradictions(
        text, snapshot, [receipt(ActionName.MESSAGE_LOGISTICS_TEAM)]
    ) == ()


def test_a_claimed_payment_that_never_confirmed_is_refused():
    snapshot = sample_snapshot(facts={"payment": "failed", "shipment": "in_transit"})
    found = reporting.contradictions(
        narrative("Payment was confirmed and the order shipped normally."), snapshot, []
    )

    assert found and "payment is recorded as failed" in found[0]


def test_a_delivery_that_did_not_happen_is_refused():
    snapshot = sample_snapshot(facts={"shipment": "delayed"})
    found = reporting.contradictions(
        narrative("The order was delivered without further incident."), snapshot, []
    )

    assert found and "shipment is recorded as delayed" in found[0]


def test_a_refund_nobody_could_have_issued_is_refused():
    snapshot = sample_snapshot(
        facts={"open_issues": [issue("refund", "Refund requested: arrived damaged")]}
    )
    found = reporting.contradictions(
        narrative("A refund was issued to the customer and the case is closed."), snapshot, []
    )

    assert any("issue a refund" in item for item in found)


def test_claiming_the_order_is_settled_while_concerns_remain_is_refused():
    snapshot = sample_snapshot(
        facts={"open_issues": [issue("shipment-delay", "Shipment delayed: hub backlog")]}
    )
    found = reporting.contradictions(
        narrative("Nothing remains unresolved on this order."), snapshot, []
    )

    assert any("shipment-delay remain open" in item for item in found)


def test_naming_an_audience_with_no_receipt_is_refused():
    snapshot = sample_snapshot()
    # An internal note was recorded; nobody was contacted.
    found = reporting.contradictions(
        narrative("We notified the logistics team about the delay and left a note."),
        snapshot,
        [receipt(ActionName.CREATE_INTERNAL_NOTE)],
    )

    assert any("logistics_team was contacted" in item for item in found)


def test_an_audience_that_does_have_a_receipt_is_allowed():
    snapshot = sample_snapshot()
    assert (
        reporting.contradictions(
            narrative("We notified the logistics team about the delay."),
            snapshot,
            [receipt(ActionName.MESSAGE_LOGISTICS_TEAM)],
        )
        == ()
    )


def test_a_claim_that_something_was_sent_outside_the_system_is_refused():
    snapshot = sample_snapshot(facts={"payment": "confirmed"})
    found = reporting.contradictions(
        narrative("We sent an email to the customer confirming the delay."),
        snapshot,
        [receipt(ActionName.MESSAGE_CUSTOMER)],
    )

    assert any("recorded, never sent" in item for item in found)


def test_learnings_and_feedback_are_checked_too_not_only_the_summary():
    snapshot = sample_snapshot(facts={"payment": "pending"})
    found = reporting.contradictions(
        ReportNarrative(
            summary="Supervision ended without incident.",
            learnings=["Payment was confirmed quickly once chased."],
        ),
        snapshot,
        [],
    )

    assert found, "a false claim hidden in a learning is still a false claim"


# ------------------------------------------------------------- adoption changes only text


def test_adoption_moves_the_prose_and_nothing_else():
    snapshot = sample_snapshot(
        facts={
            "payment": "confirmed",
            "shipment": "delivered",
            "open_issues": [issue("refund", "Refund requested: arrived damaged")],
        }
    )
    base = reporting.factual(
        snapshot,
        CloseReason.DELIVERED,
        now=RULES_NOW,
        committed=[receipt(ActionName.MESSAGE_LOGISTICS_TEAM)],
        refused=[refusal(BlockReason.REPEATED_CONTACT)],
    )
    adopted = reporting.adopt(
        base,
        narrative(
            "The parcel arrived after a carrier delay. The customer's refund question "
            "is still open and needs a person.",
            learnings=["Chasing logistics moved the shipment."],
            feedback=["Answer the refund question before closing the case."],
        ),
        model_label="groq:test-model",
    )

    assert adopted.narrative_provenance == "model_assisted"
    assert "groq:test-model" in adopted.narrative_limitation
    assert adopted.summary != base.summary
    # Everything factual is untouched by the model.
    assert adopted.close_reason == base.close_reason
    assert adopted.facts == base.facts
    assert adopted.important_actions == base.important_actions
    assert adopted.blocked_actions == base.blocked_actions
    assert adopted.unresolved_issues == base.unresolved_issues
    assert adopted.evidence_through_sequence == base.evidence_through_sequence
    # The deterministic counts survive alongside the model's observations.
    assert base.learnings[0] in adopted.learnings
    assert "Chasing logistics moved the shipment." in adopted.learnings
