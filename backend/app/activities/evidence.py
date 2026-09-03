"""Reading recorded evidence back out of the activity log.

This is the only read path the workflow has into the log, and it is read-only on purpose:
`commit_transition` remains the single writer after reservation.

There are two reads, and they are deliberately different. A decision fetches by known
sequence — no search, no scan — so what it sees is always something the run itself
recorded and chose to carry. A closing report instead needs *every* action receipt for
one run up to its frozen cutoff, because the bounded working ledger a decision carries is
not a complete account of what was done.
"""

from typing import Any

import asyncpg
from temporalio import activity

from app.contracts.decision import EvidenceBundle, EvidenceDetail, EvidenceRequest
from app.contracts.report import ReportEvidence, ReportEvidenceRequest
from app.contracts.run import (
    INTERNAL_KINDS,
    CommittedAction,
    EvidenceReference,
    RefusedAction,
)

SELECT_EVIDENCE = """
SELECT sequence, kind, disposition, recorded_at, occurred_at, event_id, action_id, explanation
FROM activity_log
WHERE run_id = $1 AND sequence = ANY($2::bigint[])
ORDER BY sequence
"""

# Committed receipts and refusals, in the order they happened. Bounded well above what a
# report will list so the reader can tell "all of them" from "the first many".
SELECT_ACTION_RECEIPTS = """
SELECT id, sequence, recorded_at, action_id, disposition, details
FROM activity_log
WHERE run_id = $1 AND kind = 'action' AND sequence <= $2
ORDER BY sequence
LIMIT 512
"""

COMMITTED_LIMIT = 128
REFUSED_LIMIT = 64


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

    @activity.defn(name="load_report_evidence")
    async def load_report_evidence(self, request: dict[str, Any]) -> dict[str, Any]:
        """Gather what was actually done, and what was refused, for a closing report."""
        parsed = ReportEvidenceRequest.model_validate(request)
        rows = await self.pool.fetch(
            SELECT_ACTION_RECEIPTS, parsed.run_id, parsed.through_sequence
        )

        committed: list[CommittedAction] = []
        refused: list[RefusedAction] = []
        unreadable = 0
        for row in rows:
            # The pool registers a jsonb codec, so `details` arrives as a dict.
            details = row["details"]
            try:
                if row["disposition"] == "committed":
                    committed.append(
                        CommittedAction(
                            action_id=row["action_id"],
                            action=details["action"],
                            content=details["content"],
                            receipt=EvidenceReference(
                                sequence=row["sequence"], activity_id=row["id"]
                            ),
                            recorded_at=row["recorded_at"],
                        )
                    )
                else:
                    refused.append(
                        RefusedAction(
                            action=details["action"],
                            reason=details["reason"],
                            explanation=row["explanation"],
                            sequence=row["sequence"],
                            recorded_at=row["recorded_at"],
                        )
                    )
            except (KeyError, TypeError, ValueError):
                # A row this reader cannot turn into a receipt is counted, never guessed
                # at and never silently dropped.
                unreadable += 1

        return ReportEvidence(
            committed=committed[:COMMITTED_LIMIT],
            refused=refused[:REFUSED_LIMIT],
            unreadable=unreadable,
            truncated=len(committed) > COMMITTED_LIMIT or len(refused) > REFUSED_LIMIT,
        ).model_dump(mode="json")
