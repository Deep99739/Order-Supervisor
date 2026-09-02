"""T03 — the atomic write boundary, against a real Postgres transaction.

These checks exist because the difference between "the same operation ran twice" and
"the same business event arrived twice" is the one mistake that would let a simulated
action be recorded twice or an applied event be lost.
"""

import pytest

from app.storage import transition as boundary
from app.storage.transition import commit_transition
from tests.conftest import event_entry, transition, with_facts

PAYLOAD = {"payment_reference": "PAY-1"}


async def test_state_and_audit_entries_commit_together(pool, reserved):
    snapshot = reserved.snapshot
    candidate = with_facts(snapshot, payment="confirmed")
    receipt = await commit_transition(
        pool,
        transition(
            snapshot,
            counter=1,
            entries=[event_entry("evt-1", PAYLOAD)],
            candidate=candidate,
        ),
    )

    assert receipt.applied is True
    assert receipt.disposition == "applied"
    assert receipt.recorded_revision == snapshot.recorded_revision + 1
    assert receipt.snapshot.facts.payment == "confirmed"
    assert receipt.snapshot.counters.unique_events == 1

    stored = await pool.fetchrow("SELECT status, snapshot, recorded_revision FROM runs")
    assert stored["recorded_revision"] == receipt.recorded_revision
    assert stored["snapshot"]["facts"]["payment"] == "confirmed"
    # The mirrored column and the stored document are one contract, enforced by CHECK.
    assert stored["status"] == stored["snapshot"]["status"]

    rows = await pool.fetch("SELECT kind FROM activity_log ORDER BY sequence")
    assert [row["kind"] for row in rows] == ["run_reserved", "event", "operation_receipt"]


async def test_lost_acknowledgement_replays_the_original_receipt(pool, reserved):
    snapshot = reserved.snapshot
    request = transition(
        snapshot,
        counter=1,
        entries=[event_entry("evt-1", PAYLOAD)],
        candidate=with_facts(snapshot, payment="confirmed"),
    )
    first = await commit_transition(pool, request)
    # The activity result was lost; Temporal retries the identical operation.
    second = await commit_transition(pool, request)

    assert second == first
    assert await pool.fetchval("SELECT count(*) FROM activity_log WHERE kind = 'event'") == 1
    assert await pool.fetchval("SELECT recorded_revision FROM runs") == first.recorded_revision
    assert second.snapshot.counters.unique_events == 1
    assert second.snapshot.counters.duplicate_events == 0


async def test_receipt_is_resolved_before_the_expected_revision_is_judged(pool, reserved):
    snapshot = reserved.snapshot
    request = transition(
        snapshot,
        counter=1,
        entries=[event_entry("evt-1", PAYLOAD)],
        candidate=with_facts(snapshot, payment="confirmed"),
    )
    first = await commit_transition(pool, request)
    # The original attempt already moved the revision past what this request expects.
    assert request.expected_recorded_revision != first.recorded_revision
    assert await commit_transition(pool, request) == first


async def test_duplicate_delivery_differs_from_an_operation_retry(pool, reserved):
    snapshot = reserved.snapshot
    applied = await commit_transition(
        pool,
        transition(
            snapshot,
            counter=1,
            entries=[event_entry("evt-1", PAYLOAD)],
            candidate=with_facts(snapshot, payment="confirmed"),
        ),
    )

    # A separate command redelivers the same business event. The workflow still built a
    # candidate; it must not be promoted.
    redelivery = transition(
        applied.snapshot,
        counter=2,
        entries=[event_entry("evt-1", PAYLOAD)],
        candidate=with_facts(applied.snapshot, payment="failed"),
        on_duplicate=[
            event_entry("evt-1-again", {"duplicate_of": "evt-1"}, disposition="duplicate")
        ],
    )
    duplicate = await commit_transition(pool, redelivery)

    assert duplicate.applied is False
    assert duplicate.disposition == "duplicate"
    assert duplicate.snapshot.facts.payment == "confirmed"
    assert duplicate.snapshot.counters.unique_events == 1
    assert duplicate.snapshot.counters.duplicate_events == 1
    assert duplicate.recorded_revision == applied.recorded_revision + 1

    # Replaying that duplicate command under a new operation must not count it twice.
    replay = transition(
        duplicate.snapshot,
        counter=3,
        entries=[event_entry("evt-1", PAYLOAD)],
        candidate=with_facts(duplicate.snapshot, payment="failed"),
        on_duplicate=[
            event_entry("evt-1-again", {"duplicate_of": "evt-1"}, disposition="duplicate")
        ],
    )
    again = await commit_transition(pool, replay)
    assert again.disposition == "duplicate"
    assert again.snapshot.counters.duplicate_events == 1
    recorded = "SELECT count(*) FROM activity_log WHERE disposition = 'duplicate'"
    assert await pool.fetchval(recorded) == 1


async def test_conflicting_identity_reuse_is_refused_and_writes_nothing(pool, reserved):
    snapshot = reserved.snapshot
    applied = await commit_transition(
        pool,
        transition(
            snapshot,
            counter=1,
            entries=[event_entry("evt-1", PAYLOAD)],
            candidate=with_facts(snapshot, payment="confirmed"),
        ),
    )
    before = await pool.fetchval("SELECT count(*) FROM activity_log")

    with pytest.raises(boundary.IdentityConflict):
        await commit_transition(
            pool,
            transition(
                applied.snapshot,
                counter=2,
                entries=[event_entry("evt-1", {"payment_reference": "PAY-DIFFERENT"})],
                candidate=with_facts(applied.snapshot, payment="failed"),
            ),
        )

    assert await pool.fetchval("SELECT count(*) FROM activity_log") == before
    assert await pool.fetchval("SELECT recorded_revision FROM runs") == applied.recorded_revision
    assert (await pool.fetchrow("SELECT snapshot FROM runs"))["snapshot"]["facts"][
        "payment"
    ] == "confirmed"


async def test_reused_operation_identity_with_different_content_is_a_conflict(pool, reserved):
    snapshot = reserved.snapshot
    request = transition(
        snapshot,
        counter=1,
        entries=[event_entry("evt-1", PAYLOAD)],
        candidate=with_facts(snapshot, payment="confirmed"),
    )
    await commit_transition(pool, request)
    changed = request.model_copy(update={"request_digest": "b" * 64})
    with pytest.raises(boundary.OperationConflict):
        await commit_transition(pool, changed)


async def test_unexplained_revision_mismatch_is_a_recovery_condition(pool, reserved):
    snapshot = reserved.snapshot
    with pytest.raises(boundary.RevisionMismatch) as failure:
        await commit_transition(
            pool,
            transition(
                snapshot,
                counter=1,
                entries=[event_entry("evt-1", PAYLOAD)],
                candidate=with_facts(snapshot, payment="confirmed"),
                expected=snapshot.recorded_revision + 5,
            ),
        )
    assert failure.value.current == snapshot.recorded_revision
    assert await pool.fetchval("SELECT count(*) FROM activity_log WHERE kind='event'") == 0
