"""T05 and T13 — who is allowed to end a run, and what a closed run leaves behind.

Completion is the assignment's sharpest rule: the agent may recommend it and never cause
it. The other half is that a deadline keeps running while the run is held or thinking.
"""

import asyncio
from uuid import uuid4

import pytest

from app.contracts.decision import ActionProposal, DecisionProposal
from app.contracts.report import ReportNarrative
from app.contracts.supervisor import SupervisorConfig
from app.domain import events as event_rules
from app.domain.presets import PRESETS
from app.domain.vocabulary import ActionName, RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration


def short_lived(seconds: int = 60) -> SupervisorConfig:
    return SupervisorConfig.model_validate(
        PRESETS[0].model_dump(mode="json")
        | {"id": str(uuid4()), "name": "Short lived", "maximum_age_seconds": seconds}
    )


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


async def test_a_recommendation_to_close_is_recorded_but_never_obeyed(pool):
    async with supervised(pool, order_id="ORD-CLOSE-ADVICE") as run:
        run.decisions.proposal = DecisionProposal(
            rationale="Everything looks settled here.",
            sleep_for_seconds=300,
            completion_recommendation="This order looks finished; consider closing it.",
        )
        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        await settled(run)
        await run.send_event("refund_requested", {"reason": "Wants to return it"})
        snapshot = await run.until(lambda state: state.counters.decisions >= 2)

        assert snapshot.status == RunStatus.SLEEPING
        assert snapshot.close_reason is None
        assert snapshot.final_output is None

        completed = [
            record
            for record in await run.entries("decision")
            if record.details.get("stage") == "completed"
        ]
        assert completed[-1].details["completion_recommendation"]


async def test_delivery_closes_the_run_with_a_factual_record(pool):
    async with supervised(pool, order_id="ORD-CLOSE-DELIVERED") as run:
        await settled(run)
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        await run.until(lambda state: state.counters.decisions == 2)

        await run.send_event("delivered", {"evidence_reference": "POD-9"})
        snapshot = await run.until(lambda state: state.status == RunStatus.COMPLETED)

        report = snapshot.final_output
        assert report is not None
        assert report.close_reason == "delivered"
        assert report.narrative_provenance == "factual_fallback"
        assert report.important_actions == [], "no action was executed in this phase"
        # A refund question does not become resolved because the parcel arrived.
        assert [issue.issue_id for issue in report.unresolved_issues] == [event_rules.REFUND_ISSUE]
        assert report.learnings and report.feedback
        assert snapshot.closed_at is not None

        result = await run.handle.result()
        assert result["close_reason"] == "delivered"


async def test_manual_termination_is_graceful_and_still_reports(pool):
    async with supervised(pool, order_id="ORD-CLOSE-TERMINATE") as run:
        await settled(run)
        await run.send_control("terminate", "Handled offline")
        snapshot = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        assert snapshot.close_reason == "manually_terminated"
        assert snapshot.final_output is not None
        # The final record is saved before the workflow returns, so wait for the return
        # itself before asking Temporal how the execution ended.
        result = await run.handle.result()
        assert result["close_reason"] == "manually_terminated"
        # Graceful: the execution completed normally rather than being killed.
        description = await run.handle.describe()
        assert description.status.name == "COMPLETED"


async def test_the_original_age_deadline_still_closes_a_held_run(pool):
    async with supervised(pool, order_id="ORD-CLOSE-AGE-PAUSED", config=short_lived(60)) as run:
        await settled(run)
        await run.send_control("pause")
        await run.until(lambda state: state.status == RunStatus.PAUSED)

        await run.advance(90)
        snapshot = await run.until(
            lambda state: state.status == RunStatus.EXPIRED, note="expiry while paused"
        )
        assert snapshot.close_reason == "maximum_age_reached"
        assert snapshot.final_output is not None


async def test_the_age_deadline_interrupts_a_review_in_progress(pool):
    """The clock cannot be skipped while an activity is outstanding, so the run is aged
    to just short of its deadline first and the last seconds pass for real."""
    async with supervised(pool, order_id="ORD-CLOSE-AGE-REVIEW", config=short_lived(60)) as run:
        await settled(run)
        await run.advance(55)

        gate = run.decisions.hold()
        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)
        try:
            snapshot = await run.until(
                lambda state: state.status == RunStatus.EXPIRED, note="expiry during a review"
            )
        finally:
            gate.set()

        assert snapshot.close_reason == "maximum_age_reached"
        assert snapshot.final_output is not None
        assert run.decisions.calls >= 2, "the review had genuinely started"


async def test_commands_admitted_after_closure_are_marked_too_late(pool):
    async with supervised(pool, order_id="ORD-CLOSE-LATE") as run:
        await settled(run)
        gate = run.decisions.hold()
        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)

        # Both land while the review is held; delivery wins and the rest arrive too late.
        await run.send_event("delivered", {"evidence_reference": "POD-1"})
        await run.send_event("customer_message_received", {"message": "Any update?"})
        gate.set()

        await run.until(lambda state: state.status == RunStatus.COMPLETED)
        records = await run.until_history(
            lambda history: any(record.disposition == "too_late" for record in history),
            note="a too-late disposition",
        )
        late = [record for record in records if record.disposition == "too_late"]
        assert late and "already closed" in late[0].explanation


async def test_a_failing_review_holds_the_run_for_recovery(pool):
    async with supervised(pool, order_id="ORD-CLOSE-RECOVERY") as run:
        await settled(run)
        run.decisions.fail_with = "The scripted provider is unavailable."
        run.decisions.calls = 0

        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        snapshot = await run.until(
            lambda state: state.status == RunStatus.AWAITING_RECOVERY, note="the recovery hold"
        )

        assert snapshot.recovery is not None
        assert snapshot.recovery.next_action == "retry_decision"
        # Bounded: two attempts for the episode, not an endless repair loop.
        assert run.decisions.calls == 2
        assert snapshot.close_reason is None, "a failed review never closes the order"
        assert snapshot.facts.shipment == "delayed", "recorded facts survive the failure"

        run.decisions.fail_with = None
        await run.send_control("resume")
        recovered = await run.until(
            lambda state: state.status == RunStatus.SLEEPING, note="recovery to complete"
        )
        assert recovered.recovery is None


# --- T13: the report is built from receipts, and the narrative is optional ----------------


def chase_logistics() -> DecisionProposal:
    return DecisionProposal(
        rationale="Chasing the open delay with logistics.",
        actions=[
            ActionProposal(
                action=ActionName.MESSAGE_LOGISTICS_TEAM,
                subject="Delayed order",
                content="Please confirm a revised delivery date for this shipment.",
                issue_id=event_rules.SHIPMENT_DELAY_ISSUE,
                rationale="The delay is open and logistics owns it.",
            )
        ],
        sleep_for_seconds=60,
    )


async def a_run_with_one_receipt(run):
    """Get one committed action onto the record, then stop proposing."""
    await settled(run)
    run.decisions.proposal = chase_logistics()
    await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
    await run.until(lambda state: state.counters.committed_actions == 1)
    run.decisions.proposal = DecisionProposal(
        rationale="Nothing further is needed.", sleep_for_seconds=300
    )


async def test_the_report_lists_receipts_read_back_from_the_record(pool):
    """The snapshot carries a bounded ledger; the report reads the log itself."""
    async with supervised(pool, order_id="ORD-CLOSE-RECEIPTS") as run:
        await a_run_with_one_receipt(run)
        await run.send_control("terminate", "Enough for now")
        snapshot = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        report = snapshot.final_output
        assert report is not None
        assert [item.action for item in report.important_actions] == [
            ActionName.MESSAGE_LOGISTICS_TEAM
        ]
        # Every listed action points at a row that exists in the log.
        receipts = {record.sequence for record in await run.history()}
        assert all(item.receipt.sequence in receipts for item in report.important_actions)
        # The cutoff is frozen before the closing transition writes its own entries, so
        # it sits below the final sequence and still covers every receipt it lists.
        assert report.evidence_through_sequence < snapshot.last_sequence
        assert all(
            item.receipt.sequence <= report.evidence_through_sequence
            for item in report.important_actions
        )


async def test_a_failed_narrative_still_saves_the_factual_report(pool):
    async with supervised(pool, order_id="ORD-CLOSE-NARRATIVE-FAILS") as run:
        await settled(run)
        run.reports.fail_with = "The provider refused the reporting request."

        await run.send_control("terminate", "Closing with reporting broken")
        snapshot = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        report = snapshot.final_output
        assert report is not None, "a delivered report is not optional because prose failed"
        assert report.narrative_provenance == "factual_fallback"
        assert report.learnings and report.feedback
        assert snapshot.counters.report_attempts == 1

        refused = [
            record
            for record in await run.entries("finalization")
            if record.details.get("stage") == "narrative"
        ]
        assert refused and refused[0].disposition == "rejected"
        assert "reporting request" in refused[0].explanation


async def test_an_accepted_narrative_changes_the_prose_and_nothing_else(pool):
    async with supervised(pool, order_id="ORD-CLOSE-NARRATIVE-OK") as run:
        await a_run_with_one_receipt(run)
        run.reports.narrative = ReportNarrative(
            summary=(
                "Supervision ended at the operator's request. A message to the logistics "
                "team was recorded about the open delay."
            ),
            learnings=["The delay needed chasing before anything moved."],
            feedback=["Confirm the revised date before this order is closed for good."],
        )

        await run.send_control("terminate", "Closing after the follow-up")
        snapshot = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        report = snapshot.final_output
        assert report.narrative_provenance == "model_assisted"
        assert "operator's request" in report.summary
        assert "scripted:test-model" in report.narrative_limitation
        # The facts stayed the workflow's.
        assert report.close_reason == "manually_terminated"
        assert len(report.important_actions) == 1
        assert snapshot.counters.report_attempts == 1

        adopted = [
            record
            for record in await run.entries("finalization")
            if record.details.get("stage") == "narrative"
        ]
        assert adopted and adopted[0].disposition == "recorded"

        # The reporting call saw the receipts, not a summary of them.
        assert run.reports.calls == 1
        request = run.reports.requests[0]
        assert [item.action for item in request.committed] == [
            ActionName.MESSAGE_LOGISTICS_TEAM
        ]
        assert request.close_reason == "manually_terminated"


async def test_reporting_is_not_counted_as_an_order_decision(pool):
    async with supervised(pool, order_id="ORD-CLOSE-REPORT-BUDGET") as run:
        snapshot = await settled(run)
        episodes = snapshot.counters.decisions

        await run.send_control("terminate", "Closing")
        closed = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        # The closing call has its own budget and does not inflate the run's reasoning.
        assert closed.counters.decisions == episodes
        assert closed.counters.model_attempts == snapshot.counters.model_attempts
