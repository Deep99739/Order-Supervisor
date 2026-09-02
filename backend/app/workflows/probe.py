from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class FoundationProbe:
    """Bounded setup diagnostic, never an order run or business effect."""

    @workflow.run
    async def run(self) -> dict[str, str]:
        return await workflow.execute_activity(
            "probe_database",
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
