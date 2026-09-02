"""T04 — one start, selective wake, and a review that fires on time alone.

These run the real workflow against a real database, so what they assert is what was
actually recorded rather than which mock was called.
"""

import pytest

from app.contracts.decision import DecisionProposal
from app.domain.vocabulary import RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration


def triggers(records):
    return [
        record.details.get("trigger")
        for record in records
        if record.kind == "decision" and record.details.get("stage") == "started"
    ]


async def test_the_run_starts_once_and_settles_into_a_scheduled_review(pool):
    async with supervised(pool, order_id="ORD-WF-START") as run:
        snapshot = await run.until(
            lambda state: state.status == RunStatus.SLEEPING,
            note="the first review to be scheduled",
        )

        assert run.decisions.calls == 1
        assert snapshot.next_wake_at is not None
        assert snapshot.counters.decisions == 1
        assert snapshot.counters.unique_events == 1
        assert snapshot.memory.text.startswith("Order ORD-WF-START")

        records = await run.history()
        assert triggers(records) == ["start"]
        # Creation evidence is recorded by the workflow, not demanded from the operator.
        creation = [record for record in records if record.kind == "event"]
        assert len(creation) == 1
        assert creation[0].details["event_type"] == "order_created"
        # The waiting state is visible before the workflow actually waits.
        assert any(record.kind == "sleep" for record in records)


async def test_a_routine_event_records_without_another_review(pool):
    async with supervised(pool, order_id="ORD-WF-ROUTINE") as run:
        first = await run.until(lambda state: state.status == RunStatus.SLEEPING)
        deadline = first.next_wake_at

        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        snapshot = await run.until(
            lambda state: state.facts.payment == "confirmed", note="the payment fact"
        )

        assert run.decisions.calls == 1, "a routine event must not wake the agent"
        assert snapshot.counters.deferred_events == 1
        # A stream of harmless updates cannot postpone the review.
        assert snapshot.next_wake_at == deadline
        assert snapshot.status == RunStatus.SLEEPING

        policy_records = await run.entries("policy")
        assert [record.disposition for record in policy_records] == ["deferred"]
        assert policy_records[0].details["wake"] is False


async def test_an_important_event_wakes_the_agent_and_can_move_the_deadline(pool):
    async with supervised(pool, order_id="ORD-WF-IMPORTANT") as run:
        first = await run.until(lambda state: state.status == RunStatus.SLEEPING)
        run.decisions.proposal = DecisionProposal(
            rationale="Chasing logistics about the delay.", sleep_for_seconds=120
        )

        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        snapshot = await run.until(
            lambda state: state.counters.decisions == 2, note="the second review"
        )

        assert run.decisions.calls == 2
        assert snapshot.facts.shipment == "delayed"
        assert snapshot.next_wake_at != first.next_wake_at
        assert triggers(await run.history()) == ["start", "important_event"]
        assert [record.disposition for record in await run.entries("policy")] == ["wake_now"]


async def test_a_due_deadline_alone_produces_a_review(pool):
    async with supervised(pool, order_id="ORD-WF-TIMER") as run:
        run.decisions.proposal = DecisionProposal(
            rationale="Still waiting on the carrier.", sleep_for_seconds=300
        )
        await run.until(lambda state: state.status == RunStatus.SLEEPING)
        assert run.decisions.calls == 1

        # Nothing is injected: only workflow time moves.
        await run.advance(310)
        snapshot = await run.until(
            lambda state: state.counters.decisions == 2, note="the scheduled review"
        )

        assert run.decisions.calls == 2
        assert snapshot.status == RunStatus.SLEEPING
        assert triggers(await run.history()) == ["start", "scheduled_wake"]


async def test_a_repeated_event_delivery_changes_nothing(pool):
    async with supervised(pool, order_id="ORD-WF-DUPLICATE") as run:
        await run.until(lambda state: state.status == RunStatus.SLEEPING)
        command = await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await run.until(lambda state: state.counters.decisions == 2)

        # A different command carrying the same source event.
        await run.send_event(
            "shipment_delayed", {"reason": "Hub backlog"}, event_id=command["event_id"]
        )
        snapshot = await run.until(
            lambda state: state.counters.duplicate_events == 1, note="the duplicate disposition"
        )

        assert snapshot.counters.unique_events == 2
        assert snapshot.counters.decisions == 2, "a repeat delivery must not trigger a review"
        duplicates = [
            record for record in await run.history() if record.disposition == "duplicate"
        ]
        assert len(duplicates) == 1
