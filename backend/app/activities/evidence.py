"""Reading recorded evidence back for a decision.

This is the only read path the workflow has into the activity log, and it is read-only on
purpose: `commit_transition` remains the single writer after reservation. It fetches by
known sequence — no search, no scan, no cross-order retrieval — so what a decision sees is
always something the run itself recorded.
"""

from typing import Any

import asyncpg
from temporalio import activity

from app.contracts.decision import EvidenceBundle, EvidenceDetail, EvidenceRequest
from app.contracts.run import INTERNAL_KINDS

SELECT_EVIDENCE = """
SELECT sequence, kind, disposition, recorded_at, occurred_at, event_id, action_id, explanation
FROM activity_log
WHERE run_id = $1 AND sequence = ANY($2::bigint[])
ORDER BY sequence
"""


class EvidenceActivities:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    @activity.defn(name="load_evidence")
    async def load_evidence(self, request: dict[str, Any]) -> dict[str, Any]:
        parsed = EvidenceRequest.model_validate(request)
        if not parsed.sequences:
            return EvidenceBundle().model_dump(mode="json")
        rows = await self.pool.fetch(SELECT_EVIDENCE, parsed.run_id, list(parsed.sequences))
        records = [
            EvidenceDetail(
                sequence=row["sequence"],
                kind=row["kind"],
                disposition=row["disposition"],
                recorded_at=row["recorded_at"],
                occurred_at=row["occurred_at"],
                event_id=row["event_id"],
                action_id=row["action_id"],
                explanation=row["explanation"],
            )
            # Operation receipts prove a write finished; they are not evidence about the
            # order, and the human timeline excludes them for the same reason.
            for row in rows
            if row["kind"] not in INTERNAL_KINDS
        ]
        return EvidenceBundle(
            records=records, missing=max(len(parsed.sequences) - len(rows), 0)
        ).model_dump(mode="json")
