"""T14 — the numbers agree with the records they claim to describe.

Analytics is where a POC most easily starts lying: a duplicate delivery counted as a
second event, a blocked proposal counted as work done, a prepared rollover counted as a
continuation, a rejected summary counted as a compaction. Each of those is a plausible
off-by-one that would make the product look busier than it was.

These drive a real run against a real database and then compare the derived numbers with
what the activity log actually holds — including the run's own cached counters, which the
response publishes a comparison of rather than trusting.
"""

import pytest

from app.contracts.decision import ActionProposal, DecisionProposal, MemoryRefresh
from app.domain import events as event_rules
from app.domain.presets import PRESETS, demo_timing
from app.domain.vocabulary import ActionName, RunStatus
from app.storage.analytics import read_analytics
from tests.harness import supervised

pytestmark = pytest.mark.integration


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


async def analytics(run):
    return await read_analytics(run.pool, await run.snapshot())


def agreed(report) -> list[str]:
    return [check.metric for check in report.counter_checks if not check.agrees]


def chase_logistics() -> DecisionProposal:
    return DecisionProposal(
        rationale="Chasing the open delay with logistics.",
        actions=[
            ActionProposal(
                action=ActionName.MESSAGE_LOGISTICS_TEAM,
                subject="Delayed order",
                content="Please confirm a revised delivery date.",
                issue_id=event_rules.SHIPMENT_DELAY_ISSUE,
                rationale="The delay is open and logistics owns it.",
            )
        ],
        sleep_for_seconds=60,
    )


async def test_the_cached_counters_agree_with_the_canonical_records(pool):
    """The whole point of publishing the comparison is that it holds."""
    async with supervised(pool, order_id="ORD-ANALYTICS-AGREE") as run:
        await settled(run)
        run.decisions.proposal = chase_logistics()
        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await run.until(lambda state: state.counters.committed_actions == 1)

        report = await analytics(run)
        assert agreed(report) == []
        assert report.through_sequence == (await run.snapshot()).last_sequence
        assert report.committed_by_action == {ActionName.MESSAGE_LOGISTICS_TEAM: 1}
        assert report.action_outcomes.committed == 1
        assert report.action_outcomes.blocked == 0


async def test_a_repeat_delivery_is_not_a_second_event(pool):
    async with supervised(pool, order_id="ORD-ANALYTICS-DUPLICATE") as run:
        await settled(run)
        original = await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        before = await run.until(lambda state: state.facts.payment == "confirmed")

        # The same source event, redelivered under a fresh command envelope.
        await run.handle.signal("event", original | {"command_id": str(run.run_id)})
        await run.until(lambda state: state.counters.duplicate_events == 1)

        report = await analytics(run)
        assert report.duplicate_events == 1
        assert report.unique_events == before.counters.unique_events
        # Identity, not row count: the redelivery reuses the same event_id.
        assert report.events_by_type["payment_confirmed"] == 1
        assert agreed(report) == []


async def test_a_blocked_proposal_is_never_counted_as_work_done(pool):
    async with supervised(pool, order_id="ORD-ANALYTICS-BLOCKED") as run:
        await settled(run)
        # A message naming a concern that is not open cannot be authorised.
        run.decisions.proposal = DecisionProposal(
            rationale="Trying to chase something that is not recorded.",
            actions=[
                ActionProposal(
                    action=ActionName.MESSAGE_LOGISTICS_TEAM,
                    subject="Where is this order",
                    content="Please confirm what happened here.",
                    issue_id="not-a-real-concern",
                    rationale="Guessing at an issue id.",
                )
            ],
            sleep_for_seconds=60,
        )
        await run.send_event("customer_message_received", {"message": "Any news?"})
        await run.until(lambda state: state.counters.decisions >= 2)

        report = await analytics(run)
        assert report.action_outcomes.blocked >= 1
        assert report.action_outcomes.committed == 0
        assert report.committed_by_action == {}
        assert "unknown_issue" in report.blocked_by_reason
        assert agreed(report) == []


async def test_a_refused_summary_is_not_counted_as_a_compaction(pool):
    async with supervised(pool, order_id="ORD-ANALYTICS-MEMORY") as run:
        await settled(run)
        # A summary claiming evidence the decision never received is refused outright.
        run.decisions.answer = lambda request: DecisionProposal(
            rationale="Refreshing the summary.",
            sleep_for_seconds=60,
            memory_refresh=MemoryRefresh(
                text="A summary reaching further than anything this decision saw.",
                through_sequence=request.context.evidence_through_sequence + 50,
            ),
        )
        await run.send_event("shipment_delayed", {"reason": "Hub backlog"})
        await run.until(lambda state: state.counters.decisions >= 2)

        report = await analytics(run)
        assert report.refused_compactions >= 1
        # The opening summary is a real version and does have a record behind it.
        assert report.compactions >= 1
        assert agreed(report) == []


async def test_the_numbers_survive_a_continuation(pool):
    """Continuing the history is maintenance; it resets neither counts nor duration."""
    config = demo_timing(PRESETS[0], "short_review")
    async with supervised(pool, order_id="ORD-ANALYTICS-ROLLOVER", config=config) as run:
        opening = await settled(run)
        before = await analytics(run)

        for _ in range(16):
            await run.send_event("no_update_for_n_hours", {"hours": 48.0})
        rolled = await run.until(
            lambda state: state.execution_generation >= 1, note="a rollover"
        )

        report = await analytics(run)
        assert report.continuations >= 1
        # A prepared rollover is recorded too, and is not the same thing.
        assert report.prepared_continuations >= report.continuations
        assert report.unique_events > before.unique_events
        assert report.started_at == opening.started_at
        assert report.duration_seconds >= before.duration_seconds
        assert agreed(report) == []
        assert rolled.counters.continuations == report.continuations


async def test_a_closed_run_reports_its_own_duration_and_reason(pool):
    async with supervised(pool, order_id="ORD-ANALYTICS-CLOSED") as run:
        await settled(run)
        await run.send_control("terminate", "Finished with it")
        closed = await run.until(lambda state: state.status == RunStatus.TERMINATED)

        report = await analytics(run)
        assert report.status == RunStatus.TERMINATED
        assert report.close_reason == "manually_terminated"
        assert report.closed_at == closed.closed_at
        assert report.duration_seconds == int(
            (closed.closed_at - closed.started_at).total_seconds()
        )
        # Nothing about supervision failing is claimed for an operator's own decision.
        assert report.operational_failures == 0
        assert agreed(report) == []


async def test_provider_attempts_are_not_reasoning_episodes(pool):
    async with supervised(pool, order_id="ORD-ANALYTICS-ATTEMPTS") as run:
        await settled(run)
        report = await analytics(run)

        # One start episode so far, and the attempts inside it are counted separately.
        assert report.decision_episodes == 1
        assert report.episodes_by_trigger.start == 1
        assert report.provider_attempts >= report.decision_episodes
        # A scripted stand-in reports no usage, so nothing is invented for it.
        assert report.tokens.input_tokens is None
        assert report.tokens.reported_calls == 0
