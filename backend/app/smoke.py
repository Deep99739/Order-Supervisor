"""Run once against the actual API and worker. No model calls or business writes."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import httpx

from app.config import load_settings
from app.connections import connect_temporal
from app.workflows.probe import FoundationProbe


async def main() -> None:
    settings = load_settings()
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8010", timeout=10) as api:
        health = await api.get("/healthz")
        health.raise_for_status()
        ready = (await api.get("/readyz")).json()
        assert ready["database"] == "available", ready
        assert ready["temporal"] == "available", ready
        schema = (await api.get("/openapi.json")).json()
        assert "/healthz" in schema["paths"] and "/readyz" in schema["paths"]
    client = await connect_temporal(settings)
    result = await client.execute_workflow(
        FoundationProbe.run,
        id=f"foundation-probe/{uuid4()}",
        task_queue=settings.temporal_task_queue,
        execution_timeout=timedelta(seconds=15),
    )
    assert result == {"database": "available", "worker": "polled_and_executed"}, result
    print(
        f"PASS API, OpenAPI, Postgres, Temporal namespace, worker activity; model={ready['model']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
