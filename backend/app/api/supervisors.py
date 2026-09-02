"""Supervisor configurations.

Saving an edit creates a new version for future runs. A run that is already in progress
keeps the snapshot it froze at creation, so changing a template never rewrites an order
that is already being supervised.
"""

from uuid import UUID

from fastapi import APIRouter

from app.api.dependencies import Pool
from app.api.errors import ApiFailure
from app.contracts.api import (
    SupervisorDraft,
    SupervisorList,
    SupervisorRecord,
    SupervisorUpdate,
)
from app.contracts.commands import ApiError
from app.storage import supervisors as store

router = APIRouter(prefix="/api", tags=["supervisors"])

ERRORS = {404: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}}


@router.get("/supervisors", response_model=SupervisorList)
async def list_supervisors(pool: Pool) -> SupervisorList:
    return SupervisorList(supervisors=await store.list_supervisors(pool))


@router.post("/supervisors", response_model=SupervisorRecord, status_code=201, responses=ERRORS)
async def create_supervisor(body: SupervisorDraft, pool: Pool) -> SupervisorRecord:
    return await store.create_supervisor(pool, body)


@router.get("/supervisors/{supervisor_id}", response_model=SupervisorRecord, responses=ERRORS)
async def read_supervisor(supervisor_id: UUID, pool: Pool) -> SupervisorRecord:
    record = await store.get_supervisor(pool, supervisor_id)
    if record is None:
        raise ApiFailure(
            404, "supervisor_not_found", "That supervisor configuration does not exist."
        )
    return record


@router.patch("/supervisors/{supervisor_id}", response_model=SupervisorRecord, responses=ERRORS)
async def update_supervisor(
    supervisor_id: UUID, body: SupervisorUpdate, pool: Pool
) -> SupervisorRecord:
    try:
        return await store.update_supervisor(pool, supervisor_id, body)
    except store.SupervisorNotFound:
        raise ApiFailure(
            404, "supervisor_not_found", "That supervisor configuration does not exist."
        ) from None
    except store.StaleConfiguration as stale:
        raise ApiFailure(
            409,
            "supervisor_version_conflict",
            f"This configuration has moved on to version {stale.current}; reload and edit again.",
            field_details={"expected_version": f"current version is {stale.current}"},
        ) from None
