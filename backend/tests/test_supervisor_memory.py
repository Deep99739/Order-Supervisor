"""T11 against a real database — what the agent is actually shown, and when.

These exist because the pure rules can be right while the wiring is wrong: a plan that
selects the correct sequences proves nothing if the read never happens, and a coverage
rule is only meaningful if the run really does stop marking things considered.
"""

import asyncio
from datetime import timedelta

import pytest

from app.contracts.decision import DecisionProposal, MemoryRefresh
from app.contracts.run import WakeGuidance, WakeHint
from app.domain import events as event_rules
from app.domain.presets import PRESETS, demo_timing
from app.domain.vocabulary import RunStatus
from tests.harness import supervised

pytestmark = pytest.mark.integration


async def settled(run):
    return await run.until(lambda state: state.status == RunStatus.SLEEPING)


# --- what a decision is shown --------------------------------------------------------------


async def test_a_deferred_event_is_put_in_front_of_the_next_decision(pool):
    async with supervised(pool, order_id="ORD-MEM-DEFERRED") as run:
        await settled(run)
        # Routine: recorded, not worth waking for.
        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        deferred = await run.until(
            lambda state: state.counters.deferred_events == 1, note="the deferred event"
        )
        assert deferred.deferred_evidence, "it is remembered as unconsidered"

        # Important: this one wakes the agent, which must now see the earlier input too.
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        await run.until(lambda state: state.counters.decisions >= 2)

        latest = run.decisions.requests[-1]
        pending = {record.sequence for record in latest.unconsidered}
        assert pending, "the deferred entry was read back and shown as unconsidered"
        assert {record.sequence for record in latest.considered}.isdisjoint(pending)
        assert all(record.explanation for record in latest.unconsidered)


async def test_considering_evidence_is_what_clears_it_not_merely_recording_it(pool):
    async with supervised(pool, order_id="ORD-MEM-COVERAGE") as run:
        await settled(run)
        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        await run.until(lambda state: state.counters.deferred_events == 1)

        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        after = await run.until(
            lambda state: state.counters.decisions >= 2 and state.status == RunStatus.SLEEPING
        )
        assert after.deferred_evidence == [], "the review covered it"
        assert after.last_decision_through_sequence > 0


async def test_an_event_arriving_mid_review_is_not_marked_considered(pool):
    """The decision's own input cutoff decides coverage, not the moment it finished.

    The probe used here is routine and changes nothing, so the episode is not stale and
    does record — which is precisely the case where a lazier rule would mark everything
    considered and quietly bury an input the review never saw.
    """
    async with supervised(pool, order_id="ORD-MEM-MIDFLIGHT") as run:
        await settled(run)

        gate = run.decisions.hold()
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        await asyncio.wait_for(run.decisions.started.wait(), timeout=10)
        await run.send_event("no_update_for_n_hours", {"hours": 48.0})
        gate.set()

        def settled_with_deferred(state):
            return state.status == RunStatus.SLEEPING and state.counters.deferred_events >= 1

        after = await run.until(settled_with_deferred, note="the mid-review event to settle")
        pending = [item.sequence for item in after.deferred_evidence]
        assert pending, "an event the review never saw is still waiting"
        assert min(pending) > after.last_decision_through_sequence

        # And the next review is handed exactly that entry as unconsidered.
        await run.send_event("refund_requested", {"reason": "Still damaged"})
        await run.until(lambda state: state.counters.decisions >= 3)
        assert {record.sequence for record in run.decisions.requests[-1].unconsidered} >= set(
            pending
        )


# --- compaction ------------------------------------------------------------------------------


def demo_run(preset=0):
    return demo_timing(PRESETS[preset], "short_review")


async def test_the_summary_is_refreshed_on_a_threshold_not_on_every_event(pool):
    async with supervised(pool, order_id="ORD-MEM-THRESHOLD") as run:
        first = await settled(run)
        assert first.memory.text, "a run always has a readable summary"
        opening = first.memory.summary_version

        await run.send_event("payment_confirmed", {"payment_reference": "PAY-1"})
        await run.until(lambda state: state.facts.payment == "confirmed")
        quiet = await run.snapshot()
        assert quiet.memory.summary_version == opening, "one routine event is not a compaction"


async def test_a_proposed_summary_is_adopted_and_labelled_as_the_agent_s(pool):
    async with supervised(pool, order_id="ORD-MEM-MODEL", config=demo_run()) as run:
        await settled(run)
        # The summary declares exactly the evidence this decision was handed.
        run.decisions.answer = lambda request: DecisionProposal(
            rationale="Summarising while I am here.",
            sleep_for_seconds=30,
            memory_refresh=MemoryRefresh(
                text="Refund raised and unresolved; payment state unknown.",
                through_sequence=request.context.evidence_through_sequence,
            ),
        )
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        after = await run.until(
            lambda state: state.memory.provenance == "model", note="the agent's summary"
        )
        assert after.memory.text.startswith("Refund raised")
        assert after.memory.source_decision_id

        record = [
            entry
            for entry in await run.entries("memory")
            if entry.details.get("provenance") == "model"
        ][0]
        assert record.details["covered_through"] == after.memory.summary_through_sequence
        assert record.details["open_issues_retained"] >= 1
        # Compaction shortens the narrative; the concern itself is untouched.
        assert event_rules.has_issue(after.facts, event_rules.REFUND_ISSUE)


async def test_a_summary_claiming_unseen_evidence_is_refused_and_the_old_one_stands(pool):
    async with supervised(pool, order_id="ORD-MEM-OVERREACH", config=demo_run()) as run:
        opening = await settled(run)
        run.decisions.proposal = DecisionProposal(
            rationale="Overreaching.",
            sleep_for_seconds=30,
            memory_refresh=MemoryRefresh(text="Everything is resolved.", through_sequence=9_000),
        )
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        await run.until(lambda state: state.counters.decisions >= 2)

        refused = [
            entry for entry in await run.entries("memory") if entry.disposition == "rejected"
        ]
        assert refused and refused[0].details["reason"] == "cutoff_beyond_input"
        latest = await run.snapshot()
        assert "Everything is resolved" not in latest.memory.text
        assert latest.memory.provenance == "deterministic"
        assert latest.memory.summary_version >= opening.memory.summary_version


# --- generated wake guidance ------------------------------------------------------------------


def watching(request, issue_id, event_type, expires_at):
    return DecisionProposal(
        rationale="I want to know as soon as the shipment moves.",
        sleep_for_seconds=300,
        wake_guidance=WakeGuidance(
            version=1,
            context=request.context,
            hints=[
                WakeHint(
                    kind="watch_for_progress",
                    issue_id=issue_id,
                    event_type=event_type,
                    expires_at=expires_at,
                )
            ],
        ),
    )


async def test_a_generated_hint_changes_what_a_later_event_does(pool):
    """The demonstration: shipment_created is ordinary progress until the agent asks."""
    async with supervised(pool, order_id="ORD-GUIDE-WATCH") as run:
        await settled(run)
        expires = (await run.now()) + timedelta(hours=2)
        run.decisions.answer = lambda request: watching(
            request, event_rules.REFUND_ISSUE, "shipment_created", expires
        )
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        adopted = await run.until(
            lambda state: state.wake_guidance is not None, note="the hint to be adopted"
        )
        assert adopted.wake_guidance.version == 1
        assert adopted.wake_guidance.source_decision_id

        run.decisions.answer = None
        reviews = adopted.counters.decisions
        await run.send_event("shipment_created", {"shipment_reference": "SHP-1"})
        woken = await run.until(
            lambda state: state.counters.decisions > reviews,
            note="the watched event to wake the agent",
        )
        assert woken.counters.deferred_events == 0, "it was not merely recorded"

        policies = await run.entries("policy")
        watched = [item for item in policies if item.details.get("guidance_hint")]
        assert watched and watched[-1].details["guidance_hint"] == "watch_for_progress"
        assert watched[-1].details["guidance_version"] == 1
        assert "asked to be woken" in watched[-1].explanation


async def test_a_hint_the_run_cannot_honour_is_refused_without_losing_the_deadline(pool):
    async with supervised(pool, order_id="ORD-GUIDE-REFUSED") as run:
        first = await settled(run)
        expires = (await run.now()) + timedelta(hours=2)
        run.decisions.answer = lambda request: watching(
            request, "invented-concern", "shipment_created", expires
        )
        await run.send_event("refund_requested", {"reason": "Arrived damaged"})
        after = await run.until(
            lambda state: state.counters.decisions > first.counters.decisions,
            note="the review to record",
        )
        assert after.wake_guidance is None, "nothing unusable was adopted"
        assert after.next_wake_at is not None, "and the run still has a next review"

        refused = [
            item for item in await run.entries("policy") if item.disposition == "rejected"
        ]
        assert refused and refused[0].details["reason"] == "unknown_issue"


async def test_a_held_run_keeps_its_summary_current_without_a_model_call(pool):
    async with supervised(pool, order_id="ORD-MEM-PAUSED", config=demo_run()) as run:
        await settled(run)
        await run.send_control("pause")
        await run.until(lambda state: state.status == RunStatus.PAUSED)
        reviews = run.decisions.calls

        for index in range(4):
            await run.send_event("customer_message_received", {"message": f"Question {index}"})
        held = await run.until(
            lambda state: event_rules.CUSTOMER_ISSUE in state.memory.text,
            note="the summary to catch up while held",
        )
        assert held.status == RunStatus.PAUSED
        assert run.decisions.calls == reviews, "no model call was spent on a summary"
        assert held.memory.provenance == "deterministic"
        assert event_rules.CUSTOMER_ISSUE in held.memory.text
