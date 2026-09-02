import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest

from app.api.errors import install_error_handlers
from app.api.runs import router as runs_router
from app.config import Settings, load_settings
from app.connections import process_connections
from app.contracts.openapi import install_contract_schemas
from app.storage.migrations import prepare_database


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: Literal["order-supervisor-api"] = "order-supervisor-api"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    checked_at: datetime
    database: Literal["available", "unavailable"]
    temporal: Literal["available", "unavailable"]
    worker: Literal["not_checked"] = "not_checked"
    model: Literal["configured_not_tested", "missing_configuration", "scripted"]
    agent_mode: Literal["live", "scripted"]
    demo_mode: bool


def create_app(
    settings: Settings | None = None,
    *,
    connections: Callable[[], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = config
        async with opener() as (pool, client):
            app.state.pool = pool
            app.state.temporal = client
            await prepare_database(pool)
            yield

    api = FastAPI(title="Order Supervisor", version="0.1.0", lifespan=lifespan)
    # Resolve settings once for CORS without requiring a DB or Temporal connection.
    config = settings or load_settings()
    opener = connections or (lambda: process_connections(config))
    api.add_middleware(
        CORSMiddleware,
        allow_origins=[config.allowed_ui_origin],
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type"],
    )

    @api.get("/healthz", response_model=HealthResponse, tags=["setup"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @api.get(
        "/readyz",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
        tags=["setup"],
    )
    async def readiness(request: Request):
        async def database_available() -> bool:
            try:
                async with asyncio.timeout(4):
                    return await request.app.state.pool.fetchval("SELECT 1") == 1
            except Exception:
                return False

        async def temporal_available() -> bool:
            try:
                async with asyncio.timeout(4):
                    await request.app.state.temporal.workflow_service.describe_namespace(
                        DescribeNamespaceRequest(namespace=config.temporal_namespace),
                        retry=False,
                        timeout=timedelta(seconds=3),
                    )
                return True
            except Exception:
                return False

        database, temporal = await asyncio.gather(database_available(), temporal_available())
        model = (
            "scripted"
            if config.agent_mode == "scripted"
            else (
                "configured_not_tested" if config.live_model_configured else "missing_configuration"
            )
        )
        ready = database and temporal and model != "missing_configuration"
        result = ReadinessResponse(
            status="ready" if ready else "degraded",
            checked_at=datetime.now(UTC),
            database="available" if database else "unavailable",
            temporal="available" if temporal else "unavailable",
            model=model,
            agent_mode=config.agent_mode,
            demo_mode=config.demo_mode,
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=200 if ready else 503)

    api.include_router(runs_router)
    install_error_handlers(api)
    install_contract_schemas(api)
    return api
