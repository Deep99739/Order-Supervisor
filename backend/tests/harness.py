"""A small harness for driving the supervisor workflow against a real database.

The persistence activity is the real one, so these tests assert what was actually
recorded. Only the decision boundary is substituted, which is what lets a test hold a
review open while an operator control or a deadline arrives.
"""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
from temporalio import activity
from temporalio.client import Client, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities.evidence import EvidenceActivities
from app.activities.persistence import PersistenceActivities
from app.contracts.decision import DecisionProposal, DecisionRequest, DecisionResult
from app.contracts.run import ActivityRecord, RunSnapshot
from app.contracts.supervisor import SupervisorConfig
from app.domain.digest import canonical_digest
from app.domain.presets import PRESETS
from app.domain.vocabulary import WORKFLOW_TYPE
from app.storage.runs import reserve_run
from app.storage.serialization import activity_from_row
from app.storage.supervisors import seed_presets
from app.workflows.order import OrderSupervisor
from app.workflows.state import WorkflowInput

SETTLE_TIMEOUT = 20.0


@dataclass
class Decisions:
    """A scripted decision boundary the test controls."""

    proposal: DecisionProposal = field(
        default_factory=lambda: DecisionProposal(
            rationale="Nothing needs doing yet.", sleep_for_seconds=300
        )
    )
    calls: int = 0
    fail_with: str | None = None
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)
    # Every request the workflow assembled, so a test can assert what a decision saw.
    requests: list[DecisionRequest] = field(default_factory=list)
    # For answers that depend on the request, such as a summary declaring its cutoff.
    answer: Callable[[DecisionRequest], DecisionProposal] | None = None

    @activity.defn(name="decide")
    async def decide(self, request: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        parsed = DecisionRequest.model_validate(request)
        self.requests.append(parsed)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_with:
            raise ApplicationError(self.fail_with, type="ScriptedFailure", non_retryable=True)
        proposal = self.answer(parsed) if self.answer else self.proposal
        return DecisionResult(proposal=proposal, provenance="scripted").model_dump(mode="json")

    def hold(self) -> asyncio.Event:
        self.gate = asyncio.Event()
        self.started.clear()
        return self.gate

    def release(self) -> None:
        if self.gate is not None:
            self.gate.set()
            self.gate = None


@dataclass
class Persistence:
    """The real write boundary, with a seam to hold one transaction open."""

    inner: PersistenceActivities
    gate: asyncio.Event | None = None
    started: asyncio.Event = field(default_factory=asyncio.Event)

    @activity.defn(name="commit_transition")
    async def commit_transition(self, request: dict[str, Any]) -> dict[str, Any]:
        if self.gate is not None:
            waiting, self.gate = self.gate, None
            self.started.set()
            await waiting.wait()
        return await self.inner.commit_transition(request)

    def hold(self) -> asyncio.Event:
        self.gate = asyncio.Event()
        self.started.clear()
        return self.gate


@dataclass
class Supervised:
    env: WorkflowEnvironment
    client: Client
    pool: asyncpg.Pool
    decisions: Decisions
    persistence: Persistence
    handle: WorkflowHandle
    run_id: UUID

    async def snapshot(self) -> RunSnapshot:
        row = await self.pool.fetchrow("SELECT snapshot FROM runs WHERE id = $1", self.run_id)
        return RunSnapshot.model_validate(row["snapshot"])

    async def history(self) -> list[ActivityRecord]:
        rows = await self.pool.fetch(
            "SELECT * FROM activity_log WHERE run_id = $1 ORDER BY sequence", self.run_id
        )
        return [activity_from_row(row) for row in rows]

    async def entries(self, kind: str) -> list[ActivityRecord]:
        return [record for record in await self.history() if record.kind == kind]

    async def until(self, predicate, *, note: str = "condition") -> RunSnapshot:
        """Poll the recorded view until the workflow has actually written something."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_TIMEOUT
        latest = await self.snapshot()
        while loop.time() < deadline:
            latest = await self.snapshot()
            if predicate(latest):
                return latest
            await asyncio.sleep(0.05)
        raise AssertionError(f"Timed out waiting for {note}; recorded state was {latest.status}")

    async def until_history(self, predicate, *, note: str = "history") -> list[ActivityRecord]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_TIMEOUT
        records: list[ActivityRecord] = []
        while loop.time() < deadline:
            records = await self.history()
            if predicate(records):
                return records
            await asyncio.sleep(0.05)
        raise AssertionError(f"Timed out waiting for {note}")

    async def send_event(self, event_type: str, payload: dict, **overrides) -> dict:
        command = {
            "command_id": str(uuid4()),
            "event_id": f"src-{uuid4().hex[:8]}",
            "event_type": event_type,
            "occurred_at": _stamp(await self.now()),
            "payload": payload,
        } | overrides
        await self.handle.signal("event", command)
        return command

    async def send_control(self, kind: str, reason: str | None = None) -> dict:
        command = {"command_id": str(uuid4()), "kind": kind, "reason": reason}
        await self.handle.signal("control", command)
        return command

    async def send_instruction(self, **fields) -> dict:
        command = {"command_id": str(uuid4()), "operation": "add"} | fields
        await self.handle.signal("instruction", command)
        return command

    async def send_review(self, draft_id: str, digest: str, decision: str = "approve") -> dict:
        command = {
            "command_id": str(uuid4()),
            "draft_id": draft_id,
            "content_digest": digest,
            "decision": decision,
        }
        await self.handle.signal("review", command)
        return command

    async def now(self) -> datetime:
        return await self.env.get_current_time()

    async def advance(self, seconds: float) -> None:
        await self.env.sleep(seconds)


def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


@asynccontextmanager
async def supervised(
    pool: asyncpg.Pool,
    *,
    config: SupervisorConfig | None = None,
    order_id: str = "ORD-WORKFLOW-1",
    context: dict | None = None,
):
    await seed_presets(pool)
    chosen = config or PRESETS[0]
    if chosen.id not in {preset.id for preset in PRESETS}:
        await pool.execute(
            "INSERT INTO supervisors (id, name, version, is_preset, config, created_at,"
            " updated_at) VALUES ($1,$2,$3,false,$4,now(),now()) ON CONFLICT (id) DO NOTHING",
            chosen.id,
            chosen.name,
            chosen.version,
            chosen.model_dump(mode="json"),
        )
    reservation = await reserve_run(
        pool,
        command_id=uuid4(),
        request_digest=canonical_digest({"order": order_id}),
        order_id=order_id,
        config=chosen,
        initial_context=context or {"description": "Workflow fixture order"},
    )
    decisions = Decisions()
    persistence = Persistence(PersistenceActivities(pool))
    env = await WorkflowEnvironment.start_time_skipping()
    try:
        queue = f"wf-{uuid4().hex[:8]}"
        async with Worker(
            env.client,
            task_queue=queue,
            workflows=[OrderSupervisor],
            activities=[
                persistence.commit_transition,
                EvidenceActivities(pool).load_evidence,
                decisions.decide,
            ],
        ):
            handle = await env.client.start_workflow(
                WORKFLOW_TYPE,
                WorkflowInput(
                    initial_event_id=reservation.initial_event_id,
                    snapshot=reservation.snapshot,
                ).model_dump(mode="json"),
                id=reservation.snapshot.workflow_id,
                task_queue=queue,
            )
            yield Supervised(
                env=env,
                client=env.client,
                pool=pool,
                decisions=decisions,
                persistence=persistence,
                handle=handle,
                run_id=reservation.snapshot.run_id,
            )
    finally:
        await env.shutdown()
