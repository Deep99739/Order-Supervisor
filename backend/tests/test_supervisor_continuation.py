"""T12 — the execution changes, the order does not.

Continue-as-new is the one moment where a long-running supervisor could quietly lose
something: a queued command, an operator's pause, the original deadline, the counters
that mint identifiers. These drive a real rollover against a real Temporal execution and
then check that none of it went missing.
"""

import asyncio
from uuid import uuid4

import pytest

from app.domain import events as event_rules
from app.domain.presets import PRESETS, demo_timing
from app.domain.vocabulary import RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration

# Enough recorded work to cross the demo history threshold.
NOISE = 16


def demo_run():
    return demo_timing(PRESETS[0], "short_review")


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


async def temporal_run_id(run) -> str:
    """The current execution behind the stable Workflow ID."""
    handle = run.client.get_workflow_handle(run.handle.id)
    return (await handle.describe()).run_id


async def accumulate(run, *, count: int = NOISE) -> None:
    """Grow the execution's history without asking the agent to do anything.

    A premature inactivity probe is recorded and deferred, so each one costs a write and
    no review — which makes the history grow predictably instead of by however much
    reasoning happened to occur.
    """
    for _ in range(count):
        await run.send_event("no_update_for_n_hours", {"hours": 48.0})
        await asyncio.sleep(0.05)


async def test_a_long_history_rolls_over_while_the_order_carries_on(pool):
    async with supervised(pool, order_id="ORD-ROLL-BASIC", config=demo_run()) as run:
        opening = await settled(run)
        first_execution = await temporal_run_id(run)

        await accumulate(run)
        rolled = await run.until(
            lambda state: state.execution_generation >= 1, note="a rollover"
        )

        # A genuinely new Temporal execution, behind the same Workflow ID.
        assert await temporal_run_id(run) != first_execution
        assert rolled.workflow_id == opening.workflow_id
        assert rolled.run_id == opening.run_id
        assert rolled.counters.continuations >= 1

        # The order's own clock is not reset by an internal boundary.
        assert rolled.started_at == opening.started_at
        assert rolled.maximum_age_at == opening.maximum_age_at

        # Lifetime counting continues rather than restarting.
        assert rolled.counters.unique_events > opening.counters.unique_events
        assert rolled.last_sequence > opening.last_sequence

        history = await run.history()
        creations = [
            record
            for record in history
            if record.kind == "event" and record.details.get("event_type") == "order_created"
        ]
        assert len(creations) == 1, "the order is not initialised a second time"

        # A preparation is not a rollover; an execution that actually resumed is. Read
        # the generation alongside the history, since the backlog may still be draining.
        current = await run.snapshot()
        resumed = [
            record
            for record in await run.history()
            if record.kind == "continuation" and record.disposition == "applied"
        ]
        assert len(resumed) >= current.execution_generation
        assert [item.details["execution_generation"] for item in resumed[:1]] == [1]
        assert "carried over unchanged" in resumed[0].explanation


async def test_a_paused_run_rolls_over_and_stays_paused(pool):
    async with supervised(pool, order_id="ORD-ROLL-PAUSED", config=demo_run()) as run:
        await settled(run)
        await run.send_instruction(text="Prioritize speed over cost.")
        await run.until(lambda state: len(state.instructions) == 1)
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        await run.until(
            lambda state: event_rules.has_issue(state.facts, event_rules.REFUND_ISSUE)
        )

        await run.send_control("pause", "Waiting on the customer team")
        paused = await run.until(lambda state: state.status == RunStatus.PAUSED)
        reviews = run.decisions.calls

        await accumulate(run)
        rolled = await run.until(
            lambda state: state.execution_generation >= 1, note="a rollover while paused"
        )

        assert rolled.status == RunStatus.PAUSED, "an internal boundary does not resume a run"
        assert rolled.control_epoch == paused.control_epoch
        assert run.decisions.calls == reviews, "and it does not reason on the way through"

        # Everything the operator and the order were relying on survived.
        assert [item.text for item in rolled.instructions] == [
            item.text for item in paused.instructions
        ]
        assert event_rules.has_issue(rolled.facts, event_rules.REFUND_ISSUE)
        assert rolled.memory.text
        assert rolled.maximum_age_at == paused.maximum_age_at

        # And it resumes normally afterwards.
        await run.send_control("resume")
        resumed = await run.until(
            lambda state: state.status == RunStatus.SLEEPING, note="the run to resume"
        )
        assert resumed.execution_generation == rolled.execution_generation


async def test_a_quiet_execution_does_not_continue_again_on_its_own(pool):
    """The threshold counts *this* execution's history, so a rollover resets it.

    A generation-wide counter would keep tripping immediately after every rollover, and
    the run would spend its life continuing instead of supervising.
    """
    async with supervised(pool, order_id="ORD-ROLL-ONCE", config=demo_run()) as run:
        await settled(run)
        await accumulate(run)
        await run.until(lambda state: state.execution_generation >= 1, note="a rollover")

        # Let the queued backlog finish; only then is the execution genuinely quiet.
        settledness = await run.snapshot()
        while True:
            await asyncio.sleep(1.0)
            latest = await run.snapshot()
            if latest.last_sequence == settledness.last_sequence:
                break
            settledness = latest

        # Nothing new arrives, so nothing should happen.
        quiet = await run.snapshot()
        await asyncio.sleep(2.0)
        after = await run.snapshot()
        assert after.execution_generation == quiet.execution_generation
        assert after.counters.continuations == quiet.counters.continuations


async def test_an_event_redelivered_after_a_rollover_is_still_a_duplicate(pool):
    """The carry does not hold every delivered identity; the database receipt does."""
    async with supervised(pool, order_id="ORD-ROLL-DUPLICATE", config=demo_run()) as run:
        await settled(run)
        original = await run.send_event("shipment_created", {"shipment_reference": "SHP-1"})
        await run.until(lambda state: state.facts.shipment == "in_transit")

        await accumulate(run)
        await run.until(lambda state: state.execution_generation >= 1, note="a rollover")
        before = await run.snapshot()

        # The source re-emits the same event under a fresh command envelope, which is
        # what a real redelivery looks like — and is distinct from retrying one command.
        await run.handle.signal("event", original | {"command_id": str(uuid4())})
        after = await run.until(
            lambda state: state.counters.duplicate_events > before.counters.duplicate_events,
            note="the redelivery to be recognised",
        )
        assert after.counters.unique_events == before.counters.unique_events
        duplicates = [
            record for record in await run.entries("event") if record.disposition == "duplicate"
        ]
        assert duplicates
