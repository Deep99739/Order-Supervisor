"""Shared fixtures. Database tests use one real Postgres schema per test and drop it."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.config import Settings, load_settings
from app.contracts.commands import EventCommand
from app.contracts.persistence import ProposedEntry, TransitionRequest
from app.contracts.run import EvidenceReference, FinalOutput, RunSnapshot
from app.domain.digest import canonical_digest
from app.domain.presets import PRESETS
from app.domain.vocabulary import operation_id, workflow_id
from app.main import create_app
from app.storage.migrations import apply_migrations
from app.storage.pool import create_pool
from app.storage.runs import Reservation, reserve_run
from app.storage.supervisors import seed_presets
from app.storage.transition import commit_transition

UNAVAILABLE = "PostgreSQL is not available; start it with docker-compose up -d --wait"


class FakeHandle:
    def __init__(self, client: "FakeTemporal", workflow_id: str):
        self.client = client
        self.workflow_id = workflow_id

    async def signal(self, name: str, payload: Any, **_: Any) -> None:
        if self.client.signal_outcome == "missing":
            raise RPCError("workflow not found", RPCStatusCode.NOT_FOUND, b"")
        if self.client.signal_outcome == "unavailable":
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        self.client.signals.append((self.workflow_id, name, payload))


class FakeTemporal:
    """A narrowly substituted client, only to control start and signal outcomes."""

    def __init__(self, *, start_outcome: str = "ok", signal_outcome: str = "ok"):
        self.start_outcome = start_outcome
        self.signal_outcome = signal_outcome
        self.starts: list[str] = []
        self.signals: list[tuple[str, str, Any]] = []

    async def start_workflow(self, workflow: str, arg: Any, *, id: str, **_: Any) -> object:
        self.starts.append(id)
        if self.start_outcome == "unavailable":
            raise RPCError("unavailable", RPCStatusCode.UNAVAILABLE, b"")
        if self.start_outcome == "lost_acknowledgement":
            raise TimeoutError("no response")
        # Like the real service under REJECT_DUPLICATE: this Workflow ID is taken.
        if self.start_outcome == "already_started" or self.starts.count(id) > 1:
            raise WorkflowAlreadyStartedError(id, workflow)
        return object()

    def get_workflow_handle(self, workflow_id: str) -> FakeHandle:
        return FakeHandle(self, workflow_id)


@pytest.fixture
def temporal() -> FakeTemporal:
    return FakeTemporal()


@asynccontextmanager
async def _client(settings: Settings, pool: asyncpg.Pool, temporal: FakeTemporal):
    @asynccontextmanager
    async def connections():
        yield pool, temporal

    app = create_app(settings, connections=connections)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def api(settings: Settings, pool: asyncpg.Pool, temporal: FakeTemporal):
    """The API as configured, but with demo timing off regardless of the local `.env`.

    A test whose result depends on a developer's environment file is a trap: it passes
    until someone flips a switch for an unrelated reason.
    """
    async with _client(settings.model_copy(update={"demo_mode": False}), pool, temporal) as client:
        yield client


@pytest.fixture
async def demo_api(settings: Settings, pool: asyncpg.Pool, temporal: FakeTemporal):
    async with _client(settings.model_copy(update={"demo_mode": True}), pool, temporal) as client:
        yield client


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


@pytest.fixture
def review_first():
    """The preset whose customer messages need a person to approve them."""
    return PRESETS[2]


RULES_RUN = UUID("7c9f0f2e-51a3-4f0e-9c2a-2b6d5a4e1c30")
RULES_NOW = datetime(2026, 9, 3, 3, 0, tzinfo=UTC)


def sample_snapshot(**overrides) -> RunSnapshot:
    """A confirmed snapshot for pure rule tests; no database involved."""
    values = {
        "run_id": RULES_RUN,
        "order_id": "ORD-RULES-1",
        "workflow_id": workflow_id(RULES_RUN),
        "supervisor": PRESETS[0],
        "initial_context": {"description": "Rule fixture order"},
        "status": "sleeping",
        "next_wake_at": RULES_NOW + timedelta(minutes=5),
        "started_at": RULES_NOW,
        "maximum_age_at": RULES_NOW + timedelta(days=1),
        "updated_at": RULES_NOW,
        "recorded_revision": 1,
        "last_sequence": 1,
    } | overrides
    return RunSnapshot.model_validate(values)


def sample_event(event_type: str, payload: dict | None = None, **overrides) -> EventCommand:
    values = {
        "command_id": uuid4(),
        "event_id": f"src-{uuid4().hex[:8]}",
        "event_type": event_type,
        "occurred_at": RULES_NOW,
        "payload": payload or {},
    } | overrides
    return EventCommand.model_validate(values)


def sample_evidence(sequence: int = 2) -> EvidenceReference:
    return EvidenceReference(sequence=sequence, activity_id=uuid4())


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


async def close_run(pool: asyncpg.Pool, snapshot: RunSnapshot, *, counter: int = 1) -> RunSnapshot:
    """Stand in for the worker finalizing a run, through the real write boundary."""
    now = datetime.now(UTC)
    final = FinalOutput(
        close_reason="delivered",
        closed_at=now,
        facts=snapshot.facts,
        summary="Delivery was recorded; supervision ended under the delivery rule.",
        important_actions=[],
        unresolved_issues=[],
        learnings=[],
        feedback=[],
        narrative_provenance="factual_fallback",
        evidence_through_sequence=snapshot.last_sequence,
    )
    candidate = RunSnapshot.model_validate(
        snapshot.model_dump(mode="json")
        | {
            "status": "completed",
            "close_reason": "delivered",
            "closed_at": now.isoformat(),
            "final_output": final.model_dump(mode="json"),
        }
    )
    entry = ProposedEntry(
        kind="finalization",
        disposition="recorded",
        explanation="Supervision ended on delivery evidence",
    )
    receipt = await commit_transition(
        pool, transition(snapshot, counter=counter, entries=[entry], candidate=candidate)
    )
    return receipt.snapshot


def with_facts(snapshot: RunSnapshot, **facts) -> RunSnapshot:
    """A candidate snapshot the workflow would build before asking to commit it."""
    document = snapshot.model_dump(mode="json")
    document["facts"].update(facts)
    document["context_version"] += 1
    document["counters"]["unique_events"] += 1
    return RunSnapshot.model_validate(document)
