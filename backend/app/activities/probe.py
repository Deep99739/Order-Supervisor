import asyncpg
from temporalio import activity


class ProbeActivities:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @activity.defn
    async def probe_database(self) -> dict[str, str]:
        await self.pool.fetchval("SELECT 1")
        return {"database": "available", "worker": "polled_and_executed"}
