"""Numbered SQL migrations applied once, in order, and recorded.

Bookkeeping is a small metadata table, not a product entity. Nothing here drops or
resets application data; a failed statement aborts its own transaction rather than
being swallowed.
"""

import logging
import re
from pathlib import Path

import asyncpg

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"
FILENAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
# One arbitrary but stable key so concurrent API and CLI starts serialize.
LOCK_KEY = 8_090_311_452_001

BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    name text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def available() -> list[tuple[int, str, str]]:
    found: list[tuple[int, str, str]] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        match = FILENAME.match(path.name)
        if not match:
            raise RuntimeError(f"Migration {path.name} must be named NNNN_lower_snake.sql")
        found.append((int(match.group(1)), match.group(2), path.read_text()))
    duplicates = {version for version, _, _ in found} ^ {index + 1 for index in range(len(found))}
    if duplicates:
        raise RuntimeError(f"Migration versions must be consecutive from 0001; check {duplicates}")
    return found


async def apply_migrations(pool: asyncpg.Pool) -> list[int]:
    """Apply pending migrations and return the versions this call committed."""
    applied: list[int] = []
    async with pool.acquire() as connection:
        async with connection.transaction():
            await connection.execute(f"SELECT pg_advisory_xact_lock({LOCK_KEY})")
            await connection.execute(BOOKKEEPING)
            rows = await connection.fetch("SELECT version FROM schema_migrations")
            recorded = {row["version"] for row in rows}
            for version, name, sql in available():
                if version in recorded:
                    continue
                await connection.execute(sql)
                await connection.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2)", version, name
                )
                applied.append(version)
    return applied


async def prepare_database(pool: asyncpg.Pool) -> None:
    """Startup convenience. An unavailable database stays a readiness problem, not a crash."""
    from app.storage.supervisors import seed_presets

    try:
        applied = await apply_migrations(pool)
        await seed_presets(pool)
    except Exception:
        logging.warning(
            "Database setup is incomplete; check PostgreSQL and run python -m app.migrate."
        )
        return
    if applied:
        logging.info("Applied migrations %s", ", ".join(str(version) for version in applied))
