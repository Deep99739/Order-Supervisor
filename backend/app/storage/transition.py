"""The atomic transition: recorded state and its effects commit together, once.

The ordering here is deliberate and load bearing:

1. An existing canonical receipt for this `operation_id` is resolved **before** the
   request's expected revision is judged, so a lost acknowledgement replays instead of
   failing on a revision the original attempt already advanced.
2. Only then does the run row get locked and the receipt check repeat, in case a retry
   raced the slow original attempt.
3. A repeated business identity carrying the same content is a redelivery; the same
   identity carrying different content is a conflict.

`recorded_revision`, `last_sequence`, `updated_at`, and `counters.duplicate_events` are
owned by this transaction. Every other field of the snapshot is the caller's proposal.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg

from app.contracts.persistence import ProposedEntry, TransitionReceipt, TransitionRequest
from app.contracts.run import RunSnapshot
from app.storage.serialization import (
    bump_duplicate_counter,
    replace_recorded_fields,
    run_columns,
    snapshot_from_row,
)


class TransitionError(Exception):
    """A condition the caller must resolve; never retried into a different outcome."""


class RunNotFound(TransitionError):
    def __init__(self, run_id: UUID):
        super().__init__(f"Run {run_id} does not exist")
        self.run_id = run_id


class RevisionMismatch(TransitionError):
    def __init__(self, expected: int, current: int):
        super().__init__(f"Expected recorded revision {expected}; the run is at {current}")
        self.expected = expected
        self.current = current


class IdentityConflict(TransitionError):
    def __init__(self, key: str):
        super().__init__(f"Identity {key} was already recorded with different content")
        self.key = key


class OperationConflict(TransitionError):
    def __init__(self, operation_id: str):
        super().__init__(f"Operation {operation_id} was recorded with a different request")
        self.operation_id = operation_id


class ReceiptUnresolvable(TransitionError):
    def __init__(self, operation_id: str):
        super().__init__(
            f"Operation {operation_id} has a receipt the current run state no longer matches"
        )
        self.operation_id = operation_id


INSERT_ENTRY = """
INSERT INTO activity_log (
    id, run_id, sequence, kind, occurred_at, recorded_at, command_id, event_id,
    operation_id, decision_id, action_id, disposition, explanation, details,
    dedupe_key, dedupe_digest
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
"""

UPDATE_RUN = """
UPDATE runs SET
    status = $2, pending_control = $3, close_reason = $4, recorded_revision = $5,
    context_version = $6, control_epoch = $7, last_sequence = $8, next_wake_at = $9,
    updated_at = $10, closed_at = $11, execution_generation = $12, snapshot = $13,
    final_output = $14
WHERE id = $1
"""


def operation_key(operation_id: str) -> str:
    return f"operation:{operation_id}"


async def _claim(connection: asyncpg.Connection, run_id: UUID, key: str) -> asyncpg.Record | None:
    return await connection.fetchrow(
        "SELECT sequence, dedupe_digest, details FROM activity_log"
        " WHERE run_id = $1 AND dedupe_key = $2",
        run_id,
        key,
    )


async def _resolve_receipt(
    connection: asyncpg.Connection, request: TransitionRequest
) -> TransitionReceipt:
    """Rebuild the original outcome of an already committed operation."""
    claimed = await _claim(connection, request.run_id, operation_key(request.operation_id))
    assert claimed is not None
    if claimed["dedupe_digest"] != request.request_digest:
        raise OperationConflict(request.operation_id)
    details = claimed["details"]
    row = await connection.fetchrow("SELECT snapshot FROM runs WHERE id = $1", request.run_id)
    if row is None:
        raise RunNotFound(request.run_id)
    snapshot = snapshot_from_row(row)
    # The workflow serializes operations, so the run should still stand where this
    # receipt left it. Anything else is a recovery condition, not a silent overwrite.
    if snapshot.recorded_revision != details["recorded_revision"]:
        raise ReceiptUnresolvable(request.operation_id)
    return TransitionReceipt(
        operation_id=request.operation_id,
        applied=details["applied"],
        disposition=details["disposition"],
        recorded_revision=details["recorded_revision"],
        last_sequence=details["last_sequence"],
        snapshot=snapshot,
    )


async def _writable(
    connection: asyncpg.Connection, run_id: UUID, entries: list[ProposedEntry]
) -> tuple[list[ProposedEntry], bool]:
    """Split entries into those not yet recorded, and whether any claim already existed."""
    fresh: list[ProposedEntry] = []
    seen = False
    for entry in entries:
        if entry.dedupe_key is None:
            fresh.append(entry)
            continue
        existing = await _claim(connection, run_id, entry.dedupe_key)
        if existing is None:
            fresh.append(entry)
            continue
        if existing["dedupe_digest"] != entry.dedupe_digest:
            raise IdentityConflict(entry.dedupe_key)
        seen = True
    return fresh, seen


async def commit_transition(pool: asyncpg.Pool, request: TransitionRequest) -> TransitionReceipt:
    async with pool.acquire() as connection:
        async with connection.transaction():
            # 1. Receipt lookup precedes revision validation.
            existing = await _claim(connection, request.run_id, operation_key(request.operation_id))
            if existing is not None:
                return await _resolve_receipt(connection, request)

            # 2. Serialize on the run row, then repeat the check.
            row = await connection.fetchrow(
                "SELECT snapshot FROM runs WHERE id = $1 FOR UPDATE", request.run_id
            )
            if row is None:
                raise RunNotFound(request.run_id)
            existing = await _claim(connection, request.run_id, operation_key(request.operation_id))
            if existing is not None:
                return await _resolve_receipt(connection, request)

            stored = snapshot_from_row(row)

            # 3. An unexplained mismatch is a recovery condition, not an overwrite.
            if stored.recorded_revision != request.expected_recorded_revision:
                raise RevisionMismatch(request.expected_recorded_revision, stored.recorded_revision)

            # 4. Distinguish a first application from a redelivered business identity.
            _, redelivered = await _writable(connection, request.run_id, request.entries)
            if redelivered:
                # The candidate the caller built is not promoted; the recorded state stands.
                entries, _ = await _writable(connection, request.run_id, request.on_duplicate)
                base: RunSnapshot = bump_duplicate_counter(stored) if entries else stored
                disposition = "duplicate"
            else:
                entries = request.entries
                base = request.snapshot
                disposition = "applied"

            # 5/6. Allocate sequences from the locked row and write everything together.
            now = datetime.now(UTC)
            sequence = stored.last_sequence
            written: list[tuple[ProposedEntry, int]] = []
            for entry in entries:
                sequence += 1
                written.append((entry, sequence))
            sequence += 1
            receipt_sequence = sequence
            revision = stored.recorded_revision + 1

            final = replace_recorded_fields(
                base, recorded_revision=revision, last_sequence=sequence, updated_at=now
            )

            for entry, entry_sequence in written:
                await connection.execute(
                    INSERT_ENTRY,
                    entry.entry_id or uuid4(),
                    request.run_id,
                    entry_sequence,
                    entry.kind,
                    entry.occurred_at,
                    now,
                    entry.command_id,
                    entry.event_id,
                    request.operation_id,
                    entry.decision_id,
                    entry.action_id,
                    entry.disposition,
                    entry.explanation,
                    entry.details,
                    entry.dedupe_key,
                    entry.dedupe_digest,
                )

            receipt_details = {
                "request_digest": request.request_digest,
                "applied": disposition == "applied",
                "disposition": disposition,
                "recorded_revision": revision,
                "last_sequence": sequence,
            }
            await connection.execute(
                INSERT_ENTRY,
                uuid4(),
                request.run_id,
                receipt_sequence,
                "operation_receipt",
                None,
                now,
                None,
                None,
                request.operation_id,
                None,
                None,
                "recorded",
                f"Operation {request.operation_id} recorded as {disposition}",
                receipt_details,
                operation_key(request.operation_id),
                request.request_digest,
            )

            columns = run_columns(final)
            await connection.execute(
                UPDATE_RUN,
                request.run_id,
                columns["status"],
                columns["pending_control"],
                columns["close_reason"],
                columns["recorded_revision"],
                columns["context_version"],
                columns["control_epoch"],
                columns["last_sequence"],
                columns["next_wake_at"],
                columns["updated_at"],
                columns["closed_at"],
                columns["execution_generation"],
                columns["snapshot"],
                columns["final_output"],
            )

            return TransitionReceipt(
                operation_id=request.operation_id,
                applied=disposition == "applied",
                disposition=disposition,
                recorded_revision=revision,
                last_sequence=sequence,
                snapshot=final,
            )
