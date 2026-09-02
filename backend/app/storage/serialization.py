"""One place that converts between rows and contracts.

The bounded `RunSnapshot` is stored whole in `runs.snapshot`. The typed columns exist so
the API can filter, order, and constrain; they are written from the same snapshot object
and the database CHECK constraints reject any pair that disagrees.
"""

from typing import Any

import asyncpg

from app.contracts.run import ActivityRecord, RunSnapshot
from app.contracts.supervisor import SupervisorConfig

RUN_COLUMNS = (
    "order_id",
    "workflow_id",
    "supervisor_id",
    "status",
    "pending_control",
    "close_reason",
    "recorded_revision",
    "context_version",
    "control_epoch",
    "last_sequence",
    "started_at",
    "maximum_age_at",
    "next_wake_at",
    "updated_at",
    "closed_at",
    "execution_generation",
    "template_snapshot",
    "initial_context",
    "snapshot",
    "final_output",
)


def run_columns(snapshot: RunSnapshot) -> dict[str, Any]:
    """Mirror the filterable values out of one snapshot, plus the snapshot itself."""
    document = snapshot.model_dump(mode="json")
    return {
        "order_id": snapshot.order_id,
        "workflow_id": snapshot.workflow_id,
        "supervisor_id": snapshot.supervisor.id,
        "status": str(snapshot.status),
        "pending_control": str(snapshot.pending_control) if snapshot.pending_control else None,
        "close_reason": str(snapshot.close_reason) if snapshot.close_reason else None,
        "recorded_revision": snapshot.recorded_revision,
        "context_version": snapshot.context_version,
        "control_epoch": snapshot.control_epoch,
        "last_sequence": snapshot.last_sequence,
        "started_at": snapshot.started_at,
        "maximum_age_at": snapshot.maximum_age_at,
        "next_wake_at": snapshot.next_wake_at,
        "updated_at": snapshot.updated_at,
        "closed_at": snapshot.closed_at,
        "execution_generation": snapshot.execution_generation,
        "template_snapshot": document["supervisor"],
        "initial_context": document["initial_context"],
        "snapshot": document,
        "final_output": document["final_output"],
    }


def snapshot_from_row(row: asyncpg.Record) -> RunSnapshot:
    return RunSnapshot.model_validate(row["snapshot"])


def supervisor_from_row(row: asyncpg.Record) -> SupervisorConfig:
    return SupervisorConfig.model_validate(row["config"])


def activity_from_row(row: asyncpg.Record) -> ActivityRecord:
    return ActivityRecord(
        id=row["id"],
        run_id=row["run_id"],
        sequence=row["sequence"],
        kind=row["kind"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
        command_id=row["command_id"],
        event_id=row["event_id"],
        operation_id=row["operation_id"],
        decision_id=row["decision_id"],
        action_id=row["action_id"],
        disposition=row["disposition"],
        explanation=row["explanation"],
        details=row["details"],
    )


def replace_recorded_fields(
    snapshot: RunSnapshot, *, recorded_revision: int, last_sequence: int, updated_at: Any
) -> RunSnapshot:
    """Rebuild with the fields the transaction owns, revalidating the whole snapshot."""
    return RunSnapshot.model_validate(
        snapshot.model_dump(mode="json")
        | {
            "recorded_revision": recorded_revision,
            "last_sequence": last_sequence,
            "updated_at": updated_at.isoformat(),
        }
    )


def bump_duplicate_counter(snapshot: RunSnapshot) -> RunSnapshot:
    """Only the transaction can observe a redelivery, so only it counts one."""
    document = snapshot.model_dump(mode="json")
    document["counters"]["duplicate_events"] += 1
    return RunSnapshot.model_validate(document)
