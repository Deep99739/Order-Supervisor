"""Apply pending migrations and seed the presets. Safe to run repeatedly."""

import asyncio
import logging

from app.config import load_settings
from app.storage.migrations import apply_migrations
from app.storage.pool import create_pool
from app.storage.supervisors import seed_presets


async def main() -> None:
    settings = load_settings()
    pool = await create_pool(settings)
    try:
        applied = await apply_migrations(pool)
        seeded = await seed_presets(pool)
    finally:
        await pool.close()
    versions = ", ".join(str(version) for version in applied) if applied else "none"
    print(f"Migrations applied: {versions}. Presets inserted: {seeded}.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except Exception:
        logging.error("Migration failed: check validated configuration and PostgreSQL.")
        raise SystemExit(1) from None
