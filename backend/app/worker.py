import asyncio
import logging
import signal
from datetime import timedelta

from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
from temporalio.worker import Worker

from app.activities.persistence import PersistenceActivities
from app.activities.probe import ProbeActivities
from app.config import load_settings
from app.connections import process_connections
from app.workflows.probe import FoundationProbe


async def run_worker() -> None:
    settings = load_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    async with process_connections(settings, worker=True) as (pool, client):
        await pool.fetchval("SELECT 1")
        await client.workflow_service.describe_namespace(
            DescribeNamespaceRequest(namespace=settings.temporal_namespace),
            retry=False,
            timeout=timedelta(seconds=3),
        )
        async with Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[FoundationProbe],
            activities=[
                ProbeActivities(pool).probe_database,
                PersistenceActivities(pool).commit_transition,
            ],
            max_concurrent_activities=4,
            graceful_shutdown_timeout=timedelta(seconds=5),
        ):
            logging.info(
                "Worker started; namespace=%s queue=%s; persistence and diagnostics only",
                settings.temporal_namespace,
                settings.temporal_task_queue,
            )
            await stop.wait()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        asyncio.run(run_worker())
    except Exception:
        # Driver exceptions may contain connection strings. Keep the process boundary concise.
        logging.error("Worker stopped: check validated configuration, PostgreSQL, and Temporal.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
