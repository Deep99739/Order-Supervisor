"""Repository behaviour: repeatable setup, versioned templates, one reservation per order."""

from uuid import uuid4

import pytest

from app.contracts.api import SupervisorDraft, SupervisorUpdate
from app.domain.digest import canonical_digest
from app.domain.presets import PRESETS, STANDARD_ID
from app.domain.vocabulary import ActionName, RunStatus
from app.storage import runs as run_store
from app.storage import supervisors as supervisor_store
from app.storage.migrations import apply_migrations
from app.storage.transition import commit_transition
from tests.conftest import event_entry, transition, with_facts

CONTEXT = {"customer_display_name": "Second customer", "description": "Another fixture order"}


def draft(**overrides) -> SupervisorDraft:
    values = {
        "name": "Weekend cover",
        "base_instructions": "Watch this order and escalate only what is blocking.",
        "allowed_actions": [ActionName.CREATE_INTERNAL_NOTE, ActionName.MESSAGE_CUSTOMER],
    } | overrides
    return SupervisorDraft(**values)


async def reserve(pool, order_id: str, command_id=None):
    command_id = command_id or uuid4()
    return await run_store.reserve_run(
        pool,
        command_id=command_id,
        request_digest=canonical_digest({"order_id": order_id}),
        order_id=order_id,
        config=PRESETS[0],
        initial_context=CONTEXT,
    )


async def test_migrations_are_repeatable(pool):
    assert await apply_migrations(pool) == []
    assert await pool.fetchval("SELECT count(*) FROM schema_migrations") == 1


async def test_presets_seed_once_and_do_not_overwrite_an_edit(pool):
    assert await supervisor_store.seed_presets(pool) == len(PRESETS)
    edited = await supervisor_store.update_supervisor(
        pool, STANDARD_ID, SupervisorUpdate(**draft().model_dump(), expected_version=1)
    )
    assert edited.config.version == 2

    assert await supervisor_store.seed_presets(pool) == 0
    unchanged = await supervisor_store.get_supervisor(pool, STANDARD_ID)
    assert unchanged is not None
    assert unchanged.config.name == "Weekend cover"
    assert unchanged.is_preset is True


async def test_presets_differ_in_behaviour_not_only_name(pool):
    await supervisor_store.seed_presets(pool)
    records = {record.config.name: record.config for record in
               await supervisor_store.list_supervisors(pool)}
    assert records["Customer review first"].customer_review_default is True
    assert records["Standard order care"].customer_review_default is False
    assert records["Urgent fulfillment"].escalate_shipment_delays is True
    assert records["Urgent fulfillment"].prioritize_speed is True
    # A shorter permitted review horizon, not just a different label.
    assert records["Urgent fulfillment"].wake_profile.default_seconds < (
        records["Standard order care"].wake_profile.default_seconds
    )


async def test_supervisor_edit_requires_the_expected_version(pool):
    created = await supervisor_store.create_supervisor(pool, draft())
    assert created.config.version == 1 and created.is_preset is False

    updated = await supervisor_store.update_supervisor(
        pool,
        created.config.id,
        SupervisorUpdate(**draft(name="Weekend cover v2").model_dump(), expected_version=1),
    )
    assert updated.config.version == 2

    with pytest.raises(supervisor_store.StaleConfiguration):
        await supervisor_store.update_supervisor(
            pool,
            created.config.id,
            SupervisorUpdate(**draft().model_dump(), expected_version=1),
        )
    with pytest.raises(supervisor_store.SupervisorNotFound):
        await supervisor_store.update_supervisor(
            pool, uuid4(), SupervisorUpdate(**draft().model_dump(), expected_version=1)
        )


async def test_repeated_creation_command_resolves_the_same_reservation(pool, reserved):
    order_id = "ORD-STORAGE-1"
    command_id = uuid4()
    first = await reserve(pool, order_id, command_id)
    again = await reserve(pool, order_id, command_id)

    assert first.created is True and again.created is False
    assert again.snapshot.run_id == first.snapshot.run_id
    assert again.initial_event_id == first.initial_event_id
    assert first.snapshot.status is RunStatus.STARTING
    assert first.snapshot.workflow_id == f"order-supervisor/{first.snapshot.run_id}"
    assert await pool.fetchval("SELECT count(*) FROM runs WHERE order_id = $1", order_id) == 1


async def test_reused_creation_command_with_different_content_is_a_conflict(pool, reserved):
    command_id = uuid4()
    await reserve(pool, "ORD-STORAGE-2", command_id)
    with pytest.raises(run_store.CreateCommandConflict):
        await run_store.reserve_run(
            pool,
            command_id=command_id,
            request_digest=canonical_digest({"order_id": "ORD-STORAGE-CHANGED"}),
            order_id="ORD-STORAGE-CHANGED",
            config=PRESETS[0],
            initial_context=CONTEXT,
        )


async def test_a_second_command_cannot_take_a_reserved_order(pool, reserved):
    first = await reserve(pool, "ORD-STORAGE-3")
    with pytest.raises(run_store.OrderAlreadyReserved) as failure:
        await reserve(pool, "ORD-STORAGE-3")
    assert failure.value.run_id == first.snapshot.run_id


async def test_run_list_pages_and_filters_by_state(pool, reserved):
    await reserve(pool, "ORD-STORAGE-4")
    await reserve(pool, "ORD-STORAGE-5")

    page, cursor = await run_store.list_runs(pool, limit=2)
    assert len(page) == 2 and cursor is not None
    rest, tail = await run_store.list_runs(pool, cursor=cursor, limit=2)
    assert tail is None
    assert len({item.run_id for item in page + rest}) == 3

    active, _ = await run_store.list_runs(pool, state="active")
    closed, _ = await run_store.list_runs(pool, state="closed")
    assert len(active) == 3 and closed == []

    found, _ = await run_store.list_runs(pool, order_id="ORD-STORAGE-4")
    assert [item.order_id for item in found] == ["ORD-STORAGE-4"]
    assert found[0].supervisor_name == PRESETS[0].name


async def test_history_is_bounded_by_a_sequence_and_hides_receipts(pool, reserved):
    snapshot = reserved.snapshot
    receipt = await commit_transition(
        pool,
        transition(
            snapshot,
            counter=1,
            entries=[event_entry("evt-1", {"payment_reference": "PAY-1"})],
            candidate=with_facts(snapshot, payment="confirmed"),
        ),
    )

    page = await run_store.read_activity(
        pool, run_id=snapshot.run_id, last_sequence=receipt.last_sequence
    )
    assert [record.kind for record in page.records] == ["run_reserved", "event"]
    assert page.last_sequence == receipt.last_sequence

    internal = await run_store.read_activity(
        pool,
        run_id=snapshot.run_id,
        last_sequence=receipt.last_sequence,
        include_internal=True,
    )
    assert "operation_receipt" in [record.kind for record in internal.records]

    # A snapshot boundary keeps a newer record out of an older facts view.
    bounded = await run_store.read_activity(
        pool, run_id=snapshot.run_id, last_sequence=receipt.last_sequence, through_sequence=1
    )
    assert [record.sequence for record in bounded.records] == [1]

    newer = await run_store.read_activity(
        pool, run_id=snapshot.run_id, last_sequence=receipt.last_sequence, after_sequence=1
    )
    assert [record.kind for record in newer.records] == ["event"]

    only_events = await run_store.read_activity(
        pool, run_id=snapshot.run_id, last_sequence=receipt.last_sequence, category="events"
    )
    assert [record.kind for record in only_events.records] == ["event"]
