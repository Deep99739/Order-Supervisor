"""Process resources opened once in the application lifespan."""

from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import Depends, Request
from temporalio.client import Client

from app.api.errors import ApiFailure
from app.config import Settings
from app.contracts.run import RunSnapshot
from app.domain.vocabulary import CLOSED_STATUS
from app.storage import runs as run_store

CLOSED = set(CLOSED_STATUS.values())


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def get_client(request: Request) -> Client:
    return request.app.state.temporal


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


Pool = Annotated[asyncpg.Pool, Depends(get_pool)]
Temporal = Annotated[Client, Depends(get_client)]
Config = Annotated[Settings, Depends(get_settings)]


async def load_run(pool: asyncpg.Pool, run_id: UUID) -> RunSnapshot:
    """Sending a command never implicitly creates a run."""
    snapshot = await run_store.get_run(pool, run_id)
    if snapshot is None:
        raise ApiFailure(404, "run_not_found", "That run does not exist.", run_id=run_id)
    return snapshot


def require_open(snapshot: RunSnapshot, command_id: UUID | None = None) -> None:
    """A convenience precheck. The workflow remains the lifecycle authority."""
    if snapshot.status in CLOSED:
        raise ApiFailure(
            409,
            "run_closed",
            f"Supervision of this order already ended ({snapshot.status}).",
            command_id=command_id,
            run_id=snapshot.run_id,
        )
