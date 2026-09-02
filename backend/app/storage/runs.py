"""Run reservation and the recorded read view.

Reservation is the only place outside `commit_transition` that writes run state. It
establishes the identities the rest of the system depends on and nothing else: the
worker's initialization transition moves `starting` forward, so the API never writes a
later lifecycle status after starting the workflow.
"""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, get_args
from uuid import UUID, uuid4

import asyncpg

from app.contracts.api import ActivityPage, RunListItem
from app.contracts.common import JsonObject
from app.contracts.run import ACTIVITY_CATEGORIES, INTERNAL_KINDS, ActivityKind, RunSnapshot
from app.contracts.supervisor import SupervisorConfig
from app.domain.vocabulary import CLOSED_STATUS, RunStatus, workflow_id
from app.storage.serialization import activity_from_row, run_columns, snapshot_from_row

CLOSED_STATUSES = [str(status) for status in CLOSED_STATUS.values()]
OPEN_STATUSES = [str(status) for status in RunStatus if status not in set(CLOSED_STATUS.values())]
ALL_KINDS: list[str] = [kind for kind in get_args(ActivityKind) if kind not in INTERNAL_KINDS]


class CreateCommandConflict(Exception):
    def __init__(self, command_id: UUID):
        super().__init__(f"Creation command {command_id} was already used with different content")
        self.command_id = command_id


class OrderAlreadyReserved(Exception):
    def __init__(self, order_id: str, run_id: UUID):
        super().__init__(f"Order {order_id} is already supervised by run {run_id}")
        self.order_id = order_id
        self.run_id = run_id


@dataclass(frozen=True)
class Reservation:
    snapshot: RunSnapshot
    initial_event_id: UUID
    created: bool


INSERT_RUN = """
INSERT INTO runs (
    id, order_id, workflow_id, supervisor_id, create_command_id, create_request_digest,
    initial_event_id, template_snapshot, initial_context, status, pending_control,
    close_reason, recorded_revision, context_version, control_epoch, last_sequence,
    started_at, maximum_age_at, next_wake_at, updated_at, closed_at, execution_generation,
    snapshot, final_output
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24)
"""

INSERT_RESERVED = """
INSERT INTO activity_log (
    id, run_id, sequence, kind, recorded_at, command_id, disposition, explanation,
    details, dedupe_key, dedupe_digest
) VALUES ($1,$2,1,'run_reserved',$3,$4,'applied',$5,$6,$7,$8)
"""

SELECT_BY_COMMAND = (
    "SELECT snapshot, initial_event_id, create_request_digest FROM runs"
    " WHERE create_command_id = $1"
)


def encode_cursor(updated_at: datetime, run_id: UUID) -> str:
    return base64.urlsafe_b64encode(f"{updated_at.isoformat()}|{run_id}".encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    raw = base64.urlsafe_b64decode(cursor.encode()).decode()
    moment, _, identity = raw.partition("|")
    return datetime.fromisoformat(moment), UUID(identity)


async def _existing_for_command(
    connection: asyncpg.Connection | asyncpg.Pool, command_id: UUID, digest: str
) -> Reservation | None:
    row = await connection.fetchrow(SELECT_BY_COMMAND, command_id)
    if row is None:
        return None
    if row["create_request_digest"] != digest:
        raise CreateCommandConflict(command_id)
    return Reservation(snapshot_from_row(row), row["initial_event_id"], created=False)


async def reserve_run(
    pool: asyncpg.Pool,
    *,
    command_id: UUID,
    request_digest: str,
    order_id: str,
    config: SupervisorConfig,
    initial_context: JsonObject,
) -> Reservation:
    """Resolve the same reservation for a repeated request; never mint a second identity."""
    for final_attempt in (False, True):
        existing = await _existing_for_command(pool, command_id, request_digest)
        if existing is not None:
            return existing
        taken = await pool.fetchrow("SELECT id FROM runs WHERE order_id = $1", order_id)
        if taken is not None:
            raise OrderAlreadyReserved(order_id, taken["id"])
        try:
            return await _insert_reservation(
                pool,
                command_id=command_id,
                request_digest=request_digest,
                order_id=order_id,
                config=config,
                initial_context=initial_context,
            )
        except asyncpg.UniqueViolationError:
            # A concurrent request won the same command or order. Resolve it, do not reset.
            if final_attempt:
                raise
    raise AssertionError("unreachable")


async def _insert_reservation(
    pool: asyncpg.Pool,
    *,
    command_id: UUID,
    request_digest: str,
    order_id: str,
    config: SupervisorConfig,
    initial_context: JsonObject,
) -> Reservation:
    run_id = uuid4()
    initial_event_id = uuid4()
    now = datetime.now(UTC)
    snapshot = RunSnapshot(
        run_id=run_id,
        order_id=order_id,
        workflow_id=workflow_id(run_id),
        supervisor=config,
        initial_context=initial_context,
        status=RunStatus.STARTING,
        recorded_revision=1,
        last_sequence=1,
        started_at=now,
        # Time spent starting counts toward the order's original age.
        maximum_age_at=now + timedelta(seconds=config.maximum_age_seconds),
        updated_at=now,
    )
    columns = run_columns(snapshot)
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(
                INSERT_RUN,
                run_id,
                columns["order_id"],
                columns["workflow_id"],
                columns["supervisor_id"],
                command_id,
                request_digest,
                initial_event_id,
                columns["template_snapshot"],
                columns["initial_context"],
                columns["status"],
                columns["pending_control"],
                columns["close_reason"],
                columns["recorded_revision"],
                columns["context_version"],
                columns["control_epoch"],
                columns["last_sequence"],
                columns["started_at"],
                columns["maximum_age_at"],
                columns["next_wake_at"],
                columns["updated_at"],
                columns["closed_at"],
                columns["execution_generation"],
                columns["snapshot"],
                columns["final_output"],
            )
            await connection.execute(
                INSERT_RESERVED,
                uuid4(),
                run_id,
                now,
                command_id,
                f"Run reserved for order {order_id}",
                {
                    "workflow_id": snapshot.workflow_id,
                    "supervisor_id": str(config.id),
                    "supervisor_version": config.version,
                    "initial_event_id": str(initial_event_id),
                    "maximum_age_at": snapshot.maximum_age_at.isoformat(),
                },
                f"command:{command_id}",
                request_digest,
            )
    return Reservation(snapshot, initial_event_id, created=True)


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> RunSnapshot | None:
    row = await pool.fetchrow("SELECT snapshot FROM runs WHERE id = $1", run_id)
    return snapshot_from_row(row) if row else None


async def get_run_start_input(pool: asyncpg.Pool, run_id: UUID) -> tuple[RunSnapshot, UUID] | None:
    row = await pool.fetchrow("SELECT snapshot, initial_event_id FROM runs WHERE id = $1", run_id)
    return (snapshot_from_row(row), row["initial_event_id"]) if row else None


async def list_runs(
    pool: asyncpg.Pool,
    *,
    state: str = "all",
    order_id: str | None = None,
    cursor: str | None = None,
    limit: int = 20,
) -> tuple[list[RunListItem], str | None]:
    statuses: list[str] | None = None
    if state == "active":
        statuses = OPEN_STATUSES
    elif state == "closed":
        statuses = CLOSED_STATUSES
    after_time, after_id = decode_cursor(cursor) if cursor else (None, None)
    rows = await pool.fetch(
        "SELECT snapshot, updated_at, id FROM runs"
        " WHERE ($1::text[] IS NULL OR status = ANY($1))"
        "   AND ($2::text IS NULL OR order_id = $2)"
        "   AND ($3::timestamptz IS NULL OR (updated_at, id) < ($3, $4))"
        " ORDER BY updated_at DESC, id DESC LIMIT $5",
        statuses,
        order_id,
        after_time,
        after_id,
        limit + 1,
    )
    more = len(rows) > limit
    page = rows[:limit]
    items = [_list_item(snapshot_from_row(row)) for row in page]
    next_cursor = encode_cursor(page[-1]["updated_at"], page[-1]["id"]) if more and page else None
    return items, next_cursor


def _list_item(snapshot: RunSnapshot) -> RunListItem:
    return RunListItem(
        run_id=snapshot.run_id,
        order_id=snapshot.order_id,
        supervisor_name=snapshot.supervisor.name,
        initial_context=snapshot.initial_context,
        status=snapshot.status,
        pending_control=snapshot.pending_control,
        close_reason=snapshot.close_reason,
        facts=snapshot.facts,
        next_wake_at=snapshot.next_wake_at,
        updated_at=snapshot.updated_at,
        closed_at=snapshot.closed_at,
    )


def selected_kinds(category: str, include_internal: bool) -> list[str]:
    kinds = list(ACTIVITY_CATEGORIES.get(category, frozenset())) or list(ALL_KINDS)
    return kinds + list(INTERNAL_KINDS) if include_internal else kinds


async def read_activity(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    last_sequence: int,
    after_sequence: int | None = None,
    before_sequence: int | None = None,
    through_sequence: int | None = None,
    category: str = "all",
    include_internal: bool = False,
    limit: int = 50,
) -> ActivityPage:
    """Ascending records bounded by one sequence, so history never outruns its snapshot."""
    kinds = selected_kinds(category, include_internal)
    bound = min(through_sequence, last_sequence) if through_sequence else last_sequence
    observed_at = datetime.now(UTC)
    if after_sequence is not None:
        rows: list[Any] = list(
            await pool.fetch(
                "SELECT * FROM activity_log WHERE run_id = $1 AND sequence > $2"
                " AND sequence <= $3 AND kind = ANY($4) ORDER BY sequence ASC LIMIT $5",
                run_id,
                after_sequence,
                bound,
                kinds,
                limit,
            )
        )
        earlier = None
    else:
        ceiling = min(before_sequence - 1, bound) if before_sequence else bound
        rows = list(
            await pool.fetch(
                "SELECT * FROM activity_log WHERE run_id = $1 AND sequence <= $2"
                " AND kind = ANY($3) ORDER BY sequence DESC LIMIT $4",
                run_id,
                ceiling,
                kinds,
                limit + 1,
            )
        )
        more = len(rows) > limit
        rows = list(reversed(rows[:limit]))
        earlier = rows[0]["sequence"] if more and rows else None
    return ActivityPage(
        records=[activity_from_row(row) for row in rows],
        earlier_cursor=earlier,
        through_sequence=bound,
        last_sequence=last_sequence,
        observed_at=observed_at,
    )
