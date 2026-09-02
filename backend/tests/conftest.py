"""Shared fixtures. Database tests use one real Postgres schema per test and drop it."""

from uuid import UUID, uuid4

import asyncpg
import pytest

from app.config import Settings, load_settings
from app.contracts.persistence import ProposedEntry, TransitionRequest
from app.contracts.run import RunSnapshot
from app.domain.digest import canonical_digest
from app.domain.presets import PRESETS
from app.domain.vocabulary import operation_id
from app.storage.migrations import apply_migrations
from app.storage.pool import create_pool
from app.storage.runs import Reservation, reserve_run
from app.storage.supervisors import seed_presets

UNAVAILABLE = "PostgreSQL is not available; start it with docker-compose up -d --wait"


@pytest.fixture(scope="session")
def settings() -> Settings:
    try:
        return load_settings()
    except RuntimeError as error:  # pragma: no cover - depends on local setup
        pytest.skip(f"{error}")


@pytest.fixture
async def pool(settings: Settings):
    url = settings.database_url.get_secret_value()
    try:
        admin = await asyncpg.connect(url, timeout=3)
    except Exception:
        pytest.skip(UNAVAILABLE)
    schema = f"test_{uuid4().hex[:12]}"
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.close()
    connections = await create_pool(settings, schema=schema)
    await apply_migrations(connections)
    try:
        yield connections
    finally:
        await connections.close()
        cleanup = await asyncpg.connect(url, timeout=5)
        await cleanup.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await cleanup.close()


@pytest.fixture
async def reserved(pool: asyncpg.Pool) -> Reservation:
    await seed_presets(pool)
    command_id = uuid4()
    context = {"customer_display_name": "Test customer", "description": "Fixture order"}
    request = {"supervisor_id": str(PRESETS[0].id), "order_id": "ORD-FIXTURE-1", "context": context}
    return await reserve_run(
        pool,
        command_id=command_id,
        request_digest=canonical_digest(request),
        order_id="ORD-FIXTURE-1",
        config=PRESETS[0],
        initial_context=context,
    )


def event_entry(event_id: str, payload: dict, *, disposition: str = "applied") -> ProposedEntry:
    return ProposedEntry(
        kind="event",
        disposition=disposition,
        explanation=f"Event {event_id} {disposition}",
        event_id=event_id,
        dedupe_key=f"event:{event_id}",
        dedupe_digest=canonical_digest(payload),
        details=payload,
    )


def transition(
    snapshot: RunSnapshot,
    *,
    counter: int,
    entries: list[ProposedEntry],
    on_duplicate: list[ProposedEntry] | None = None,
    candidate: RunSnapshot | None = None,
    expected: int | None = None,
    run_id: UUID | None = None,
) -> TransitionRequest:
    identity = run_id or snapshot.run_id
    return TransitionRequest(
        run_id=identity,
        operation_id=operation_id(identity, counter),
        request_digest=canonical_digest({"counter": counter, "entries": len(entries)}),
        expected_recorded_revision=(
            snapshot.recorded_revision if expected is None else expected
        ),
        snapshot=candidate or snapshot,
        entries=entries,
        on_duplicate=on_duplicate or [],
    )


def with_facts(snapshot: RunSnapshot, **facts) -> RunSnapshot:
    """A candidate snapshot the workflow would build before asking to commit it."""
    document = snapshot.model_dump(mode="json")
    document["facts"].update(facts)
    document["context_version"] += 1
    document["counters"]["unique_events"] += 1
    return RunSnapshot.model_validate(document)
