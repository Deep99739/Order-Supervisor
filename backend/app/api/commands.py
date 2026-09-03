"""Operator commands for an existing run.

Every route here validates the request shape, confirms the run exists and is still open,
and forwards one signal. The response says the signal was accepted and that processing is
still pending — never that an event was applied or an action was taken. The application
receipt in the run's history establishes what actually happened.
"""

from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import Pool, Temporal, load_run, require_open
from app.api.errors import ApiFailure
from app.api.transport import signal_run
from app.contracts.commands import (
    ApiError,
    CommandAcknowledgement,
    ControlCommand,
    EventCommand,
    InstructionCommand,
    ReviewCommand,
)
from app.domain.vocabulary import (
    CONTROL_SIGNAL,
    EVENT_SIGNAL,
    INSTRUCTION_SIGNAL,
    REVIEW_SIGNAL,
    ControlKind,
)

router = APIRouter(prefix="/api/runs/{run_id}", tags=["commands"])

ERRORS = {
    404: {"model": ApiError},
    409: {"model": ApiError},
    422: {"model": ApiError},
    503: {"model": ApiError},
}
ACCEPTED = {"response_model": CommandAcknowledgement, "status_code": 202, "responses": ERRORS}

# Interrupt and pause are one persistent hold, so both spellings arrive as `pause`.
PAUSE_ALIASES = {ControlKind.PAUSE, ControlKind.INTERRUPT}


async def _forward(
    pool, client, run_id: UUID, signal: str, command_id: UUID, payload: dict
) -> CommandAcknowledgement:
    snapshot = await load_run(pool, run_id)
    require_open(snapshot, command_id)
    await signal_run(
        client,
        workflow_id=snapshot.workflow_id,
        signal=signal,
        payload=payload,
        command_id=command_id,
        run_id=run_id,
    )
    return CommandAcknowledgement(command_id=command_id, run_id=run_id)


async def _control(
    pool,
    client,
    run_id: UUID,
    body: ControlCommand,
    allowed: set[ControlKind],
    applied: ControlKind,
) -> CommandAcknowledgement:
    if body.kind not in allowed:
        raise ApiFailure(
            422,
            "control_kind_mismatch",
            f"This endpoint applies {applied}; the request asked for {body.kind}.",
            field_details={"kind": f"expected one of {sorted(str(kind) for kind in allowed)}"},
            command_id=body.command_id,
            run_id=run_id,
        )
    command = ControlCommand(command_id=body.command_id, kind=applied, reason=body.reason)
    return await _forward(
        pool, client, run_id, CONTROL_SIGNAL, body.command_id, command.model_dump(mode="json")
    )


@router.post("/events", **ACCEPTED)
async def submit_event(
    run_id: UUID, body: EventCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    """A valid but unfamiliar event type is accepted as evidence, not rejected."""
    return await _forward(
        pool, client, run_id, EVENT_SIGNAL, body.command_id, body.model_dump(mode="json")
    )


@router.post("/instructions", **ACCEPTED)
async def submit_instruction(
    run_id: UUID, body: InstructionCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    return await _forward(
        pool, client, run_id, INSTRUCTION_SIGNAL, body.command_id, body.model_dump(mode="json")
    )


@router.post("/interrupt", **ACCEPTED)
async def interrupt_run(
    run_id: UUID, body: ControlCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    return await _control(pool, client, run_id, body, PAUSE_ALIASES, ControlKind.PAUSE)


@router.post("/pause", **ACCEPTED)
async def pause_run(
    run_id: UUID, body: ControlCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    """Alias of interrupt; exactly the same persistent hold."""
    return await _control(pool, client, run_id, body, PAUSE_ALIASES, ControlKind.PAUSE)


@router.post("/resume", **ACCEPTED)
async def resume_run(
    run_id: UUID, body: ControlCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    return await _control(pool, client, run_id, body, {ControlKind.RESUME}, ControlKind.RESUME)


@router.post("/terminate", **ACCEPTED)
async def terminate_run(
    run_id: UUID, body: ControlCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    """Graceful, workflow-handled termination; never Temporal's hard termination."""
    return await _control(
        pool, client, run_id, body, {ControlKind.TERMINATE}, ControlKind.TERMINATE
    )


# `:path` because a draft identity is `{run_id}/draft/{n}`, following the same family as
# every other generated identifier. Without it the slashes make the route unreachable.
@router.post("/reviews/{draft_id:path}", **ACCEPTED)
async def review_draft(
    run_id: UUID, draft_id: str, body: ReviewCommand, pool: Pool, client: Temporal
) -> CommandAcknowledgement:
    """Approval names one exact draft; a replacement message cannot ride along."""
    if body.draft_id != draft_id:
        raise ApiFailure(
            422,
            "review_target_mismatch",
            "The approved draft must be the one named in the path.",
            field_details={"draft_id": "does not match the request path"},
            command_id=body.command_id,
            run_id=run_id,
        )
    return await _forward(
        pool, client, run_id, REVIEW_SIGNAL, body.command_id, body.model_dump(mode="json")
    )
