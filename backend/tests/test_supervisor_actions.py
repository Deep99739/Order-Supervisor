"""T08 and T09 against a real database — what an action actually leaves behind.

The gate's rules are covered as pure functions elsewhere. What can only be seen here is
whether an authorised proposal becomes a receipt that points at a row that exists, whether
the run's own record of who was contacted survives into the next episode, and whether a
review that the world moved past is let go of instead of applied.
"""

import asyncio

import pytest

from app.contracts.decision import ActionProposal, DecisionProposal
from app.domain import events as event_rules
from app.domain.vocabulary import ActionName, BlockReason, RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


def chase_logistics(**fields) -> DecisionProposal:
    values = {
        "action": ActionName.MESSAGE_LOGISTICS_TEAM,
        "subject": "Delayed order",
        "content": "Please confirm a revised delivery date for this shipment.",
        "issue_id": event_rules.SHIPMENT_DELAY_ISSUE,
        "rationale": "The delay is open and logistics owns it.",
    } | fields
    return DecisionProposal(
        rationale="Chasing the open delay with logistics.",
        actions=[ActionProposal.model_validate(values)],
        sleep_for_seconds=60,
    )


async def delay_the_order(run):
    """Open a shipment-delay concern the proposals can legitimately reference."""
    await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
    return await run.until(
        lambda state: event_rules.has_issue(state.facts, event_rules.SHIPMENT_DELAY_ISSUE),
        note="the delay to be recorded",
    )


# --- a committed action is a row, not a claim ---------------------------------------------


async def test_an_authorised_proposal_becomes_a_receipt_pointing_at_a_real_row(pool):
    async with supervised(pool, order_id="ORD-ACT-COMMIT") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()
        await delay_the_order(run)
        snapshot = await run.until(
            lambda state: state.counters.committed_actions == 1, note="the action to commit"
        )

        committed = [
            record for record in await run.entries("action") if record.disposition == "committed"
        ]
        assert len(committed) == 1
        record = committed[0]
        assert record.details["audience"] == "logistics_team"
        assert record.details["simulated"] is True
        assert record.details["executed"] is True
        assert record.details["subject"] == "Delayed order"

        # The receipt the run carries names the entry that was actually written.
        [carried] = snapshot.committed_actions
        assert carried.action_id == record.action_id
        assert carried.receipt.sequence == record.sequence
        assert carried.receipt.activity_id == record.id


async def test_a_committed_contact_is_remembered_against_the_concern(pool):
    async with supervised(pool, order_id="ORD-ACT-LEDGER") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()
        await delay_the_order(run)
        snapshot = await run.until(lambda state: state.counters.committed_actions == 1)

        [issue] = [
            item
            for item in snapshot.facts.open_issues
            if item.issue_id == event_rules.SHIPMENT_DELAY_ISSUE
        ]
        [contact] = issue.contacts
        assert contact.audience == "logistics_team"
        assert issue.last_action_id == contact.action_id
        assert contact.follow_up_at > contact.contacted_at


async def test_the_same_contact_about_unchanged_work_is_refused_next_time(pool):
    """Transport idempotency cannot prevent this: a later episode has a new action id."""
    async with supervised(pool, order_id="ORD-ACT-REPEAT") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()
        await delay_the_order(run)
        await run.until(lambda state: state.counters.committed_actions == 1)

        # A scheduled review comes round with nothing new to say.
        await run.advance(120)
        snapshot = await run.until(
            lambda state: state.counters.decisions >= 3, note="the scheduled review"
        )
        assert snapshot.counters.committed_actions == 1, "no second message about the same work"

        blocked = [
            record for record in await run.entries("action") if record.disposition == "blocked"
        ]
        assert blocked and blocked[0].details["reason"] == BlockReason.REPEATED_CONTACT
        assert blocked[0].details["executed"] is False


async def test_a_proposal_naming_no_real_concern_is_refused_and_recorded(pool):
    async with supervised(pool, order_id="ORD-ACT-UNKNOWN-ISSUE") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics(issue_id="invented-concern")
        await delay_the_order(run)
        records = await run.until_history(
            lambda history: any(record.disposition == "blocked" for record in history),
            note="the refusal",
        )

        blocked = [record for record in records if record.disposition == "blocked"]
        assert blocked[0].details["reason"] == BlockReason.UNKNOWN_ISSUE
        assert (await run.snapshot()).counters.committed_actions == 0


# --- a review the world moved past --------------------------------------------------------


async def test_a_review_overtaken_by_new_facts_is_discarded_not_applied(pool):
    async with supervised(pool, order_id="ORD-ACT-STALE") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()

        gate = run.decisions.hold()
        await delay_the_order(run)
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)
        # Arrives while the model is thinking, and changes what is known.
        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        gate.set()

        records = await run.until_history(
            lambda history: any(
                record.kind == "decision" and record.disposition == "rejected"
                for record in history
            ),
            note="the stale review to be discarded",
        )
        discarded = [
            record
            for record in records
            if record.kind == "decision" and record.disposition == "rejected"
        ]
        assert "context changed" in discarded[0].explanation
        assert discarded[0].details["reason"] == BlockReason.STALE_CONTEXT

        # A discarded episode is not counted as a decision, and its proposal never
        # became an effect. The run reassesses under the same trigger instead.
        settled_again = await run.until(
            lambda state: state.status == RunStatus.SLEEPING and state.counters.decisions >= 2,
            note="the reassessment",
        )
        assert settled_again.facts.payment == "confirmed"
        assert run.decisions.calls >= 3, "the review was genuinely run again"
        assert settled_again.counters.committed_actions <= 1


# --- customer contact under review --------------------------------------------------------


def message_customer(**fields) -> DecisionProposal:
    values = {
        "action": ActionName.MESSAGE_CUSTOMER,
        "content": "Your parcel is delayed at the hub. We have asked for a new date.",
        "issue_id": event_rules.SHIPMENT_DELAY_ISSUE,
        "rationale": "The customer does not know about the delay yet.",
    } | fields
    return DecisionProposal(
        rationale="Telling the customer about the delay.",
        actions=[ActionProposal.model_validate(values)],
        sleep_for_seconds=600,
    )


async def draft_waiting(run):
    run.decisions.proposal = message_customer()
    await delay_the_order(run)
    return await run.until(
        lambda state: state.pending_review is not None, note="the customer draft"
    )


async def test_a_customer_message_waits_as_a_draft_instead_of_being_recorded(pool, review_first):
    async with supervised(pool, order_id="ORD-ACT-DRAFT", config=review_first) as run:
        await settled(run)
        snapshot = await draft_waiting(run)

        draft = snapshot.pending_review
        assert draft.status == "pending"
        assert draft.content.startswith("Your parcel is delayed")
        assert snapshot.counters.committed_actions == 0, "nothing reached the customer"

        waiting = [
            record
            for record in await run.entries("action")
            if record.disposition == "pending_review"
        ]
        assert waiting[0].details["reason"] == BlockReason.APPROVAL_REQUIRED
        assert waiting[0].details["executed"] is False


async def test_approving_the_exact_draft_records_the_message_once(pool, review_first):
    async with supervised(pool, order_id="ORD-ACT-APPROVE", config=review_first) as run:
        await settled(run)
        draft = (await draft_waiting(run)).pending_review

        await run.send_review(draft.draft_id, draft.content_digest)
        snapshot = await run.until(
            lambda state: state.counters.committed_actions == 1, note="the approved message"
        )
        assert snapshot.pending_review is None, "the approval is spent, not reusable"
        [committed] = snapshot.committed_actions
        assert committed.action_id == draft.action_id

        record = [
            item for item in await run.entries("action") if item.disposition == "committed"
        ][0]
        assert record.details["review"] == "approved"
        assert record.details["audience"] == "customer"

        # Clicking approve again cannot authorise a second message.
        await run.send_review(draft.draft_id, draft.content_digest)
        await run.until_history(
            lambda history: sum(
                record.kind == "review" and record.disposition == "rejected"
                for record in history
            )
            == 1,
            note="the repeated approval to be refused",
        )
        assert (await run.snapshot()).counters.committed_actions == 1


async def test_approval_cannot_carry_a_different_message(pool, review_first):
    async with supervised(pool, order_id="ORD-ACT-DIGEST", config=review_first) as run:
        await settled(run)
        draft = (await draft_waiting(run)).pending_review

        await run.send_review(draft.draft_id, "b" * 64)
        records = await run.until_history(
            lambda history: any(record.disposition == "conflict" for record in history),
            note="the mismatched approval",
        )
        conflict = [record for record in records if record.disposition == "conflict"][0]
        assert "not the draft that is waiting" in conflict.explanation
        assert (await run.snapshot()).counters.committed_actions == 0


async def test_new_facts_invalidate_a_draft_that_was_never_approved(pool, review_first):
    async with supervised(pool, order_id="ORD-ACT-OUTDATED", config=review_first) as run:
        await settled(run)
        draft = (await draft_waiting(run)).pending_review

        await run.send_event("shipment_created", {"shipment_reference": "SHP-2"})
        snapshot = await run.until(
            lambda state: state.pending_review is not None
            and state.pending_review.status == "outdated",
            note="the draft to go stale",
        )
        assert snapshot.counters.committed_actions == 0

        # The old approval is no longer usable either.
        await run.send_review(draft.draft_id, draft.content_digest)
        await run.until_history(
            lambda history: any(record.disposition == "rejected" for record in history),
            note="the refusal of a stale approval",
        )
        assert (await run.snapshot()).counters.committed_actions == 0


async def test_approval_while_paused_waits_for_resume_before_it_takes_effect(pool, review_first):
    async with supervised(pool, order_id="ORD-ACT-PAUSED-APPROVAL", config=review_first) as run:
        await settled(run)
        draft = (await draft_waiting(run)).pending_review

        await run.send_control("pause", "Checking with the customer team")
        await run.until(lambda state: state.status == RunStatus.PAUSED)

        await run.send_review(draft.draft_id, draft.content_digest)
        held = await run.until(
            lambda state: state.pending_review is not None
            and state.pending_review.status in {"approved", "outdated"},
            note="the approval to be recorded",
        )
        assert held.counters.committed_actions == 0, "approval is not an effect"

        assert held.pending_review.status == "approved"

        # Resuming revalidates the draft against current facts and only then releases it.
        await run.send_control("resume")
        resumed = await run.until(
            lambda state: state.counters.committed_actions == 1, note="the approved message"
        )
        assert resumed.pending_review is None
        assert resumed.committed_actions[0].action_id == draft.action_id


async def test_a_closing_run_reports_what_was_recorded_and_what_was_not(pool):
    async with supervised(pool, order_id="ORD-ACT-REPORT") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()
        await delay_the_order(run)
        await run.until(lambda state: state.counters.committed_actions == 1)

        await run.send_event("delivered", {"evidence_reference": "POD-7"})
        snapshot = await run.until(lambda state: state.status == RunStatus.COMPLETED)

        report = snapshot.final_output
        assert [item.action for item in report.important_actions] == [
            ActionName.MESSAGE_LOGISTICS_TEAM
        ]
        assert all(item.simulated for item in report.important_actions)
        assert "1 simulated action(s) were recorded" in report.summary
        assert any("logistics_team" in line for line in report.learnings)
        assert any("simulation" in line for line in report.feedback)
