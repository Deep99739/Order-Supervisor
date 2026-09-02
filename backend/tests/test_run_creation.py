"""T02 — one order, one reservation, one stable Workflow ID.

PostgreSQL and Temporal are not one transaction. These checks pin down what happens in
the gap: a repeated request, a lost start acknowledgement, an unavailable Temporal, and a
second command naming an order that is already supervised.
"""

from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.service import RPCError
from temporalio.worker import Worker

from app.activities.persistence import PersistenceActivities
from app.domain.presets import PRESETS, STANDARD_ID
from app.main import create_app
from app.storage.runs import get_run
from app.workflows.order import OrderSupervisor
from tests.conftest import FakeTemporal, close_run

CONTEXT = {"customer_display_name": "Ada", "description": "One synthetic order"}


def creation(order_id: str, command_id=None, **overrides) -> dict:
    return {
        "command_id": str(command_id or uuid4()),
        "supervisor_id": str(STANDARD_ID),
        "order_id": order_id,
        "initial_context": CONTEXT,
    } | overrides


async def test_creation_reserves_one_identity_and_starts_it(api, temporal: FakeTemporal):
    body = creation("ORD-CREATE-1")
    response = await api.post("/api/runs", json=body)

    assert response.status_code == 201
    created = response.json()
    assert created["start"] == "started"
    assert created["status"] == "starting"
    assert created["workflow_id"] == f"order-supervisor/{created['run_id']}"
    assert temporal.starts == [created["workflow_id"]]

    # The API leaves `starting` for the worker's initialization transition to move.
    view = (await api.get(f"/api/runs/{created['run_id']}")).json()
    assert view["snapshot"]["status"] == "starting"
    assert view["snapshot"]["supervisor"]["name"] == PRESETS[0].name
    assert view["snapshot"]["recorded_revision"] == 1

    history = (await api.get(f"/api/runs/{created['run_id']}/activity")).json()
    assert [record["kind"] for record in history["records"]] == ["run_reserved"]


async def test_repeating_the_same_request_resolves_the_same_run(api, temporal: FakeTemporal):
    body = creation("ORD-CREATE-2")
    first = await api.post("/api/runs", json=body)
    second = await api.post("/api/runs", json=body)

    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["run_id"] == first.json()["run_id"]
    assert second.json()["start"] == "started"
    # The second attempt found the Workflow ID taken and resolved the same reservation.
    assert temporal.starts == [first.json()["workflow_id"]] * 2
    listed = (await api.get("/api/runs", params={"order_id": "ORD-CREATE-2"})).json()
    assert len(listed["runs"]) == 1


async def test_lost_start_acknowledgement_is_retryable_with_the_same_identity(api, temporal):
    temporal.start_outcome = "lost_acknowledgement"
    body = creation("ORD-CREATE-3")
    uncertain = await api.post("/api/runs", json=body)

    assert uncertain.status_code == 202
    assert uncertain.json()["start"] == "retry_required"
    assert uncertain.json()["start_detail"]
    reserved_run = uncertain.json()["run_id"]

    temporal.start_outcome = "ok"
    retried = await api.post("/api/runs", json=body)
    assert retried.status_code == 200
    assert retried.json()["run_id"] == reserved_run
    assert retried.json()["start"] == "started"
    assert await api.get(f"/api/runs/{reserved_run}") is not None


async def test_unavailable_temporal_never_claims_the_run_started(api, temporal):
    temporal.start_outcome = "unavailable"
    response = await api.post("/api/runs", json=creation("ORD-CREATE-4"))

    assert response.status_code == 202
    assert response.json()["start"] == "retry_required"
    view = (await api.get(f"/api/runs/{response.json()['run_id']}")).json()
    assert view["snapshot"]["status"] == "starting"


async def test_a_second_command_cannot_supervise_a_reserved_order(api):
    first = await api.post("/api/runs", json=creation("ORD-CREATE-5"))
    clash = await api.post("/api/runs", json=creation("ORD-CREATE-5"))

    assert clash.status_code == 409
    assert clash.json()["code"] == "order_already_supervised"
    assert clash.json()["run_id"] == first.json()["run_id"]
    assert clash.json()["retryable"] is False


async def test_a_closed_order_is_not_restarted_by_a_later_request(api, pool):
    created = (await api.post("/api/runs", json=creation("ORD-CREATE-6"))).json()
    snapshot = await get_run(pool, UUID(created["run_id"]))
    assert snapshot is not None
    await close_run(pool, snapshot)

    clash = await api.post("/api/runs", json=creation("ORD-CREATE-6"))
    assert clash.status_code == 409
    assert clash.json()["run_id"] == created["run_id"]
    still_closed = await pool.fetchval("SELECT status FROM runs WHERE id = $1", created["run_id"])
    assert still_closed == "completed"


async def test_reusing_a_command_id_for_different_content_is_a_conflict(api):
    command_id = uuid4()
    await api.post("/api/runs", json=creation("ORD-CREATE-7", command_id))
    changed = await api.post("/api/runs", json=creation("ORD-CREATE-8", command_id))

    assert changed.status_code == 409
    assert changed.json()["code"] == "creation_command_conflict"


async def test_unknown_supervisor_and_ungated_demo_timing_are_refused(api):
    missing = await api.post(
        "/api/runs", json=creation("ORD-CREATE-9", supervisor_id=str(uuid4()))
    )
    assert missing.status_code == 404
    assert missing.json()["code"] == "supervisor_not_found"

    demo = await api.post(
        "/api/runs", json=creation("ORD-CREATE-10", demo_timing_preset="short_expiry")
    )
    assert demo.status_code == 422
    assert demo.json()["code"] == "demo_timing_unavailable"
    assert await api.get("/api/runs", params={"order_id": "ORD-CREATE-10"}) is not None


@pytest.mark.integration
async def test_start_is_idempotent_against_a_real_temporal_service(settings, pool):
    """One workflow integration: the reserved Workflow ID resolves to one execution."""
    try:
        client = await Client.connect(
            settings.temporal_address, namespace=settings.temporal_namespace
        )
        await client.service_client.check_health()
    except Exception:
        pytest.skip("Temporal is not available; start it with docker-compose up -d --wait")

    queue = f"test-{uuid4().hex[:8]}"
    scoped = settings.model_copy(update={"temporal_task_queue": queue})

    from contextlib import asynccontextmanager

    import httpx

    @asynccontextmanager
    async def connections():
        yield pool, client

    async with Worker(
        client,
        task_queue=queue,
        workflows=[OrderSupervisor],
        activities=[PersistenceActivities(pool).commit_transition],
    ):
        app = create_app(scoped, connections=connections)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
                body = creation("ORD-INTEGRATION-1")
                first = await http.post("/api/runs", json=body)
                second = await http.post("/api/runs", json=body)

        assert first.status_code == 201 and second.status_code == 200
        run_id = first.json()["run_id"]
        assert second.json()["run_id"] == run_id

        handle = client.get_workflow_handle(first.json()["workflow_id"])
        description = await handle.describe()
        assert description.status.name == "RUNNING"
        # The immutable input reached the execution with its reserved creation identity.
        reserved = await pool.fetchval(
            "SELECT initial_event_id FROM runs WHERE id = $1", run_id
        )
        assert reserved is not None

        await handle.terminate(reason="test cleanup")
        with pytest.raises(RPCError):
            await client.get_workflow_handle(f"order-supervisor/{uuid4()}").describe()
