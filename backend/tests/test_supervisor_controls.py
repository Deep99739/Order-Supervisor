"""T06, T07 and T10 — operator control and unfamiliar events.

The dangerous moments are the overlapping ones: a hold arriving while the agent is mid
review, a hold arriving after a transaction was already dispatched, and an unfamiliar
event landing while the run is held. Each of those has one correct answer and several
plausible wrong ones.
"""

import asyncio

import pytest

from app.contracts.decision import ActionProposal, DecisionProposal
from app.domain import events as event_rules
from app.domain.vocabulary import ActionName, RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


async def test_a_hold_during_a_review_discards_its_conclusions(pool):
    async with supervised(pool, order_id="ORD-CTL-INFERENCE") as run:
        first = await settled(run)

        # Hold the review open, then let an operator intervene while it is running.
        gate = run.decisions.hold()
        run.decisions.proposal = DecisionProposal(
            rationale="Chase logistics hard.",
            actions=[
                ActionProposal(
                    action=ActionName.MESSAGE_LOGISTICS_TEAM,
                    subject="Delayed order",
                    content="Please confirm a revised delivery date.",
                    issue_id=event_rules.SHIPMENT_DELAY_ISSUE,
                    rationale="The delay is open.",
                )
            ],
            sleep_for_seconds=60,
        )
        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)

        await run.send_control("pause", "Operator stepping in")
        gate.set()

        snapshot = await run.until(
            lambda state: state.status == RunStatus.PAUSED, note="the pause to take effect"
        )
        assert snapshot.counters.decisions == 1, "the interrupted review must not count"
        # The deadline that review would have set never took hold.
        assert snapshot.next_wake_at == first.next_wake_at
        # And neither did the message it wanted to send.
        assert snapshot.counters.committed_actions == 0
        assert not any(
            record.disposition == "committed" for record in await run.entries("action")
        )

        discarded = [
            record
            for record in await run.entries("decision")
            if record.disposition == "rejected"
        ]
        assert len(discarded) == 1
        assert "no longer apply" in discarded[0].explanation


async def test_a_terminal_event_during_a_review_closes_the_run(pool):
    async with supervised(pool, order_id="ORD-CTL-TERMINAL") as run:
        await settled(run)
        gate = run.decisions.hold()
        await run.send_event("refund_requested", {"reason": "Damaged"})
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)

        await run.send_event("delivered", {"evidence_reference": "POD-1"})
        gate.set()

        snapshot = await run.until(
            lambda state: state.status == RunStatus.COMPLETED, note="closure on delivery"
        )
        assert snapshot.close_reason == "delivered"
        assert snapshot.final_output is not None
        # Delivery settles the shipment, not the customer's refund question.
        open_ids = [issue.issue_id for issue in snapshot.facts.open_issues]
        assert event_rules.REFUND_ISSUE in open_ids


async def test_a_hold_after_dispatch_shows_pausing_until_the_receipt_settles(pool):
    async with supervised(pool, order_id="ORD-CTL-DISPATCH") as run:
        await settled(run)

        # Hold the next write open and pause while that transaction is in flight.
        gate = run.persistence.hold()
        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        await asyncio.wait_for(run.persistence.started.wait(), timeout=10)
        await run.send_control("pause")
        gate.set()

        await run.until(lambda state: state.status == RunStatus.PAUSED)

        controls = await run.entries("control")
        stages = [record.details.get("stage") for record in controls]
        # Pausing is recorded first; paused only once the dispatched write settled.
        assert stages[0] == "pausing"
        assert "Paused" in controls[1].explanation
        assert controls[0].sequence < controls[1].sequence


async def test_a_replayed_hold_after_resume_does_not_pause_again(pool):
    async with supervised(pool, order_id="ORD-CTL-REPLAY") as run:
        await settled(run)
        hold = await run.send_control("pause")
        paused = await run.until(lambda state: state.status == RunStatus.PAUSED)

        await run.send_control("resume")
        resumed = await run.until(
            lambda state: state.control_epoch > paused.control_epoch, note="the resume"
        )

        # The original pause command is delivered again, out of order.
        await run.handle.signal("control", hold)
        await asyncio.sleep(0.5)
        latest = await run.snapshot()

        assert latest.status != RunStatus.PAUSED
        assert latest.control_epoch == resumed.control_epoch, "no new control boundary"


async def test_events_and_instructions_are_recorded_while_held(pool):
    async with supervised(pool, order_id="ORD-CTL-HELD") as run:
        await settled(run)
        await run.send_control("pause")
        paused = await run.until(lambda state: state.status == RunStatus.PAUSED)
        reviews_before = paused.counters.decisions

        await run.send_event("shipment_delayed", {"reason": "Weather"})
        await run.send_instruction(text="If shipment is delayed, escalate immediately.")
        snapshot = await run.until(
            lambda state: len(state.instructions) == 1, note="the instruction while paused"
        )

        assert snapshot.status == RunStatus.PAUSED
        assert snapshot.facts.shipment == "delayed"
        assert snapshot.counters.decisions == reviews_before, "no agent work while held"
        assert run.decisions.calls == 1

        # Resuming assesses everything accumulated, once.
        await run.send_control("resume")
        resumed = await run.until(
            lambda state: state.counters.decisions == reviews_before + 1, note="one reassessment"
        )
        assert resumed.status == RunStatus.SLEEPING
        assert run.decisions.calls == 2


async def test_an_unfamiliar_event_escalates_and_waits_when_held(pool):
    async with supervised(pool, order_id="ORD-CTL-UNKNOWN") as run:
        await settled(run)

        await run.send_event("warehouse_exception", {"bay": "B12", "note": "Pallet damaged"})
        snapshot = await run.until(
            lambda state: state.counters.decisions == 2, note="attention for the unknown event"
        )
        flagged = [issue for issue in snapshot.facts.open_issues if issue.review_required]
        assert [issue.issue_id for issue in flagged] == ["unknown:warehouse_exception"]
        assert [record.disposition for record in await run.entries("policy")] == [
            "review_required"
        ]

        await run.send_control("pause")
        await run.until(lambda state: state.status == RunStatus.PAUSED)
        reviews = (await run.snapshot()).counters.decisions

        await run.send_event("courier_exception", {"code": "X9"})
        held = await run.until(
            lambda state: any(
                issue.issue_id == "unknown:courier_exception" for issue in state.facts.open_issues
            ),
            note="the unknown event recorded while paused",
        )
        assert held.status == RunStatus.PAUSED
        assert held.counters.decisions == reviews, "escalation waits for resume"


async def test_an_unfamiliar_payload_cannot_invent_order_state(pool):
    async with supervised(pool, order_id="ORD-CTL-INJECT") as run:
        await settled(run)
        await run.send_event(
            "warehouse_exception",
            {"payment": "confirmed", "shipment": "delivered", "status": "completed"},
        )
        snapshot = await run.until(
            lambda state: bool(state.facts.open_issues), note="the unknown event"
        )
        assert snapshot.facts.payment == "unknown"
        assert snapshot.facts.shipment == "unknown"
        assert snapshot.status != RunStatus.COMPLETED
