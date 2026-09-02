"""Supervisor configurations. FastAPI owns these writes; a run uses a frozen copy."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg

from app.contracts.api import SupervisorDraft, SupervisorRecord, SupervisorUpdate
from app.domain.presets import PRESETS
from app.storage.serialization import supervisor_from_row


class SupervisorNotFound(Exception):
    def __init__(self, identity: UUID):
        super().__init__(f"Supervisor {identity} does not exist")
        self.identity = identity


class StaleConfiguration(Exception):
    def __init__(self, identity: UUID, current: int):
        super().__init__(f"Supervisor {identity} is at version {current}")
        self.identity = identity
        self.current = current


INSERT = """
INSERT INTO supervisors (id, name, version, is_preset, config, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $6)
"""

SELECT = "SELECT id, is_preset, config, created_at, updated_at FROM supervisors"


def _record(row: asyncpg.Record) -> SupervisorRecord:
    return SupervisorRecord(
        config=supervisor_from_row(row),
        is_preset=row["is_preset"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def seed_presets(pool: asyncpg.Pool) -> int:
    """Idempotent by stable identifier; an operator's later edits are never overwritten."""
    now = datetime.now(UTC)
    inserted = 0
    async with pool.acquire() as connection:
        for config in PRESETS:
            status = await connection.execute(
                f"{INSERT} ON CONFLICT (id) DO NOTHING",
                config.id,
                config.name,
                config.version,
                True,
                config.model_dump(mode="json"),
                now,
            )
            inserted += int(status.rsplit(" ", 1)[-1])
    return inserted


async def list_supervisors(pool: asyncpg.Pool) -> list[SupervisorRecord]:
    rows = await pool.fetch(f"{SELECT} ORDER BY is_preset DESC, name ASC, id ASC")
    return [_record(row) for row in rows]


async def get_supervisor(pool: asyncpg.Pool, identity: UUID) -> SupervisorRecord | None:
    row = await pool.fetchrow(f"{SELECT} WHERE id = $1", identity)
    return _record(row) if row else None


async def create_supervisor(pool: asyncpg.Pool, draft: SupervisorDraft) -> SupervisorRecord:
    identity = uuid4()
    config = draft.to_config(identity, 1)
    now = datetime.now(UTC)
    await pool.execute(
        INSERT, identity, config.name, config.version, False, config.model_dump(mode="json"), now
    )
    return SupervisorRecord(config=config, is_preset=False, created_at=now, updated_at=now)


async def update_supervisor(
    pool: asyncpg.Pool, identity: UUID, update: SupervisorUpdate
) -> SupervisorRecord:
    """Save a new version for future runs. Active runs keep their frozen snapshot."""
    config = update.to_config(identity, update.expected_version + 1)
    now = datetime.now(UTC)
    row = await pool.fetchrow(
        "UPDATE supervisors SET name = $2, version = $3, config = $4, updated_at = $5"
        " WHERE id = $1 AND version = $6"
        " RETURNING id, is_preset, config, created_at, updated_at",
        identity,
        config.name,
        config.version,
        config.model_dump(mode="json"),
        now,
        update.expected_version,
    )
    if row is not None:
        return _record(row)
    existing = await get_supervisor(pool, identity)
    if existing is None:
        raise SupervisorNotFound(identity)
    raise StaleConfiguration(identity, existing.config.version)
