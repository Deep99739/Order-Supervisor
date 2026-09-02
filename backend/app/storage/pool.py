import json

import asyncpg

from app.config import Settings


async def _register_json(connection: asyncpg.Connection) -> None:
    # Work with dictionaries at the repository boundary rather than raw JSON text.
    for name in ("json", "jsonb"):
        await connection.set_type_codec(
            name, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def create_pool(settings: Settings, *, schema: str | None = None) -> asyncpg.Pool:
    # min_size=0 lets the API expose a useful 503 when Postgres is unavailable.
    return await asyncpg.create_pool(
        settings.database_url.get_secret_value(),
        min_size=0,
        max_size=4,
        timeout=3,
        command_timeout=10,
        init=_register_json,
        server_settings={"search_path": schema} if schema else None,
    )
