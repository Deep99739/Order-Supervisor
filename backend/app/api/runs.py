"""Run creation and the recorded read view."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import Config, Pool, Temporal, load_run
from app.api.errors import ApiFailure
from app.api.transport import start_supervisor
from app.contracts.api import ActivityPage, RunCreated, RunPage, RunView
from app.contracts.commands import ApiError, CreateRunRequest
from app.domain.digest import canonical_digest
from app.domain.presets import demo_timing
from app.storage import runs as run_store
from app.storage import supervisors as supervisor_store

router = APIRouter(prefix="/api", tags=["runs"])

ERRORS = {
    404: {"model": ApiError},
    409: {"model": ApiError},
    422: {"model": ApiError},
    503: {"model": ApiError},
}


@router.post("/runs", response_model=RunCreated, status_code=201, responses=ERRORS)
async def create_run(
    body: CreateRunRequest, response: Response, pool: Pool, client: Temporal, settings: Config
) -> RunCreated:
    """Reserve the order once, then start its stable Workflow ID.

    PostgreSQL and Temporal share no transaction, so the two steps are separate and the
    start is retryable against the same reservation. `starting` is left for the worker's
    initialization transition to move forward.
    """
    record = await supervisor_store.get_supervisor(pool, body.supervisor_id)
    if record is None:
        raise ApiFailure(
            404,
            "supervisor_not_found",
            "That supervisor configuration does not exist.",
            command_id=body.command_id,
        )
    if body.demo_timing_preset and not settings.demo_mode:
        raise ApiFailure(
            422,
            "demo_timing_unavailable",
            "Demo timing presets require DEMO_MODE=true in the backend environment.",
            field_details={"demo_timing_preset": "not enabled for this process"},
            command_id=body.command_id,
        )

    config = demo_timing(record.config, body.demo_timing_preset)
    request_digest = canonical_digest(
        {
            "supervisor_id": str(body.supervisor_id),
            "order_id": body.order_id,
            "initial_context": body.initial_context,
            "demo_timing_preset": body.demo_timing_preset,
        }
    )
    try:
        reservation = await run_store.reserve_run(
            pool,
            command_id=body.command_id,
            request_digest=request_digest,
            order_id=body.order_id,
            config=config,
            initial_context=body.initial_context,
        )
    except run_store.CreateCommandConflict:
        raise ApiFailure(
            409,
            "creation_command_conflict",
            "That command_id was already used to create a different run.",
            command_id=body.command_id,
        ) from None
    except run_store.OrderAlreadyReserved as taken:
        raise ApiFailure(
            409,
            "order_already_supervised",
            f"Order {taken.order_id} already has a supervisor run.",
            command_id=body.command_id,
            run_id=taken.run_id,
        ) from None

    start, detail = await start_supervisor(
        client, settings, reservation.snapshot, reservation.initial_event_id
    )
    if start == "retry_required":
        response.status_code = 202
    elif not reservation.created:
        response.status_code = 200
    return RunCreated(
        command_id=body.command_id,
        run_id=reservation.snapshot.run_id,
        order_id=reservation.snapshot.order_id,
        workflow_id=reservation.snapshot.workflow_id,
        status=reservation.snapshot.status,
        start=start,
        start_detail=detail,
    )


@router.get("/runs", response_model=RunPage, responses=ERRORS)
async def list_runs(
    pool: Pool,
    state: Literal["active", "closed", "all"] = "all",
    order_id: str | None = None,
    cursor: str | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> RunPage:
    try:
        items, next_cursor = await run_store.list_runs(
            pool, state=state, order_id=order_id, cursor=cursor, limit=limit
        )
    except (ValueError, TypeError):
        raise ApiFailure(
            422, "invalid_cursor", "That page cursor could not be read; start from the first page."
        ) from None
    return RunPage(runs=items, next_cursor=next_cursor, observed_at=datetime.now(UTC))


@router.get("/runs/{run_id}", response_model=RunView, responses=ERRORS)
async def read_run(run_id: UUID, pool: Pool) -> RunView:
    snapshot = await load_run(pool, run_id)
    return RunView(snapshot=snapshot, observed_at=datetime.now(UTC))


@router.get("/runs/{run_id}/activity", response_model=ActivityPage, responses=ERRORS)
async def read_activity(
    run_id: UUID,
    pool: Pool,
    after_sequence: int | None = Query(None, ge=0),
    before_sequence: int | None = Query(None, ge=1),
    through_sequence: int | None = Query(None, ge=0),
    category: Literal["all", "events", "actions", "system"] = "all",
    include_internal: bool = False,
    limit: int = Query(50, ge=1, le=100),
) -> ActivityPage:
    """`through_sequence` bounds history to one snapshot, so a newer receipt is never
    merged into an older view of the order's facts."""
    snapshot = await load_run(pool, run_id)
    return await run_store.read_activity(
        pool,
        run_id=run_id,
        last_sequence=snapshot.last_sequence,
        after_sequence=after_sequence,
        before_sequence=before_sequence,
        through_sequence=through_sequence,
        category=category,
        include_internal=include_internal,
        limit=limit,
    )
