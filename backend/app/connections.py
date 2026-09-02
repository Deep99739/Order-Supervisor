import asyncio
from contextlib import asynccontextmanager

from temporalio.client import Client

from app.config import Settings
from app.storage.pool import create_pool


async def connect_temporal(settings: Settings, *, lazy: bool = True) -> Client:
    return await Client.connect(
        settings.temporal_address,
        namespace=settings.temporal_namespace,
        lazy=lazy,
    )


@asynccontextmanager
async def process_connections(settings: Settings, *, worker: bool = False):
    pool = await create_pool(settings)
    try:
        async with asyncio.timeout(5):
            client = await connect_temporal(settings, lazy=not worker)
        yield pool, client
    finally:
        try:
            await asyncio.wait_for(pool.close(), timeout=5)
        except TimeoutError:
            pool.terminate()
        # The Temporal SDK client has no close() API. Its runtime is process-scoped.
