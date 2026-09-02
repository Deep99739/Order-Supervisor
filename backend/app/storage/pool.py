import asyncpg

from app.config import Settings


async def create_pool(settings: Settings) -> asyncpg.Pool:
    # min_size=0 lets the API expose a useful 503 when Postgres is unavailable.
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=0,
        max_size=4,
        timeout=3,
        command_timeout=3,
    )
