"""The workflow's only route to the database.

Activity retries are safe: the same `operation_id` resolves the original receipt instead
of writing a second time. Conditions the caller must resolve are non-retryable, so the
workflow decides what to do rather than the SDK repeating a doomed attempt.
"""

from typing import Any

import asyncpg
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.contracts.persistence import TransitionRequest
from app.storage import transition


class PersistenceActivities:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @activity.defn(name="commit_transition")
    async def commit_transition(self, request: dict[str, Any]) -> dict[str, Any]:
        parsed = TransitionRequest.model_validate(request)
        try:
            receipt = await transition.commit_transition(self.pool, parsed)
        except transition.TransitionError as error:
            raise ApplicationError(
                str(error), type=type(error).__name__, non_retryable=True
            ) from None
        return receipt.model_dump(mode="json")
