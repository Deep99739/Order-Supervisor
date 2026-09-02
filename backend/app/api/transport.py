"""Talking to Temporal.

Two rules hold everywhere here. A start that is not confirmed is retryable with the same
reserved identity, never with a new one. A signal that is not confirmed is never reported
as accepted; the caller retries with the original command identity.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from app.api.errors import ApiFailure
from app.config import Settings
from app.contracts.run import RunSnapshot
from app.domain.vocabulary import WORKFLOW_TYPE
from app.workflows.state import WorkflowInput

START_TIMEOUT = timedelta(seconds=5)
SIGNAL_TIMEOUT = timedelta(seconds=5)

UNCONFIRMED_START = (
    "The workflow start was not confirmed. Retry this request with the same command_id."
)


async def start_supervisor(
    client: Client,
    settings: Settings,
    snapshot: RunSnapshot,
    initial_event_id: UUID,
) -> tuple[str, str | None]:
    """Start the reserved Workflow ID. Returns the start state and any concise reason."""
    payload = WorkflowInput(
        initial_event_id=initial_event_id, snapshot=snapshot
    ).model_dump(mode="json")
    try:
        await client.start_workflow(
            WORKFLOW_TYPE,
            payload,
            id=snapshot.workflow_id,
            task_queue=settings.temporal_task_queue,
            # A finished order is not started again, and a live one is not replaced.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
            rpc_timeout=START_TIMEOUT,
        )
        return "started", None
    except WorkflowAlreadyStartedError:
        # This reservation's workflow already exists. Resolve it; never generate another.
        return "started", None
    except RPCError as failure:
        if failure.status is RPCStatusCode.ALREADY_EXISTS:
            return "started", None
        return "retry_required", UNCONFIRMED_START
    except (TimeoutError, OSError):
        return "retry_required", UNCONFIRMED_START


async def signal_run(
    client: Client,
    *,
    workflow_id: str,
    signal: str,
    payload: dict[str, Any],
    command_id: UUID,
    run_id: UUID,
) -> None:
    # Addressed by the stable Workflow ID, never a remembered Temporal Run ID.
    handle = client.get_workflow_handle(workflow_id)
    try:
        await handle.signal(signal, payload, rpc_timeout=SIGNAL_TIMEOUT)
    except RPCError as failure:
        if failure.status is RPCStatusCode.NOT_FOUND:
            raise ApiFailure(
                409,
                "run_not_accepting_commands",
                "This run has no running workflow execution, so the command was not accepted.",
                command_id=command_id,
                run_id=run_id,
            ) from None
        raise ApiFailure(
            503,
            "temporal_unavailable",
            "Acceptance was not confirmed. Retry with the same command_id.",
            retryable=True,
            command_id=command_id,
            run_id=run_id,
        ) from None
    except (TimeoutError, OSError):
        raise ApiFailure(
            503,
            "temporal_unavailable",
            "Acceptance was not confirmed. Retry with the same command_id.",
            retryable=True,
            command_id=command_id,
            run_id=run_id,
        ) from None
