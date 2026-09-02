"""The runtime write boundary.

After reservation, one serialized workflow path owns every mutation through
`commit_transition`. The workflow proposes a next snapshot and its audit entries; the
transaction decides whether that proposal is a first application or a redelivery, and
returns the canonical receipt that a retry replays.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from app.contracts.common import (
    ActivityDetails,
    Count,
    Digest,
    Reference,
    ShortText,
    UTCDateTime,
    WireModel,
)
from app.contracts.run import ActivityDisposition, ActivityKind, RunSnapshot

# Prefixed so a command, an event, an action, and an operation cannot collide.
DedupeKey = Annotated[
    str, StringConstraints(pattern=r"^(command|event|action|operation):[^\s]{1,180}$")
]

ENTRY_BATCH = 64
DUPLICATE_BATCH = 8


class ProposedEntry(WireModel):
    """One audit entry. A `dedupe_key` marks this entry as canonical for that identity."""

    # Supplied by the workflow (deterministically) when it needs to reference this entry as
    # evidence in the same transition; the transaction assigns one otherwise.
    entry_id: UUID | None = None
    kind: ActivityKind
    disposition: ActivityDisposition
    explanation: ShortText
    occurred_at: UTCDateTime | None = None
    command_id: UUID | None = None
    event_id: Reference | None = None
    decision_id: Reference | None = None
    action_id: Reference | None = None
    dedupe_key: DedupeKey | None = None
    dedupe_digest: Digest | None = None
    details: ActivityDetails = Field(default_factory=dict)

    @model_validator(mode="after")
    def claims_are_comparable(self):
        # A claimed identity always carries its content digest, so redelivery of the same
        # content and reuse of the identity for different content stay distinguishable.
        if (self.dedupe_key is None) != (self.dedupe_digest is None):
            raise ValueError("a claimed identity requires both a dedupe key and its digest")
        return self


def _unique_keys(entries: list[ProposedEntry], label: str) -> None:
    keys = [entry.dedupe_key for entry in entries if entry.dedupe_key]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{label} cannot claim the same identity twice")


class TransitionRequest(WireModel):
    """`recorded_revision`, `last_sequence`, and `updated_at` on the proposed snapshot are
    replaced by the transaction; every other field is the workflow's proposal."""

    run_id: UUID
    operation_id: Reference
    request_digest: Digest
    expected_recorded_revision: Count
    snapshot: RunSnapshot
    entries: Annotated[list[ProposedEntry], Field(max_length=ENTRY_BATCH)] = Field(
        default_factory=list
    )
    # Written instead of `entries` when a claimed business identity was already recorded.
    on_duplicate: Annotated[list[ProposedEntry], Field(max_length=DUPLICATE_BATCH)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def coherent_request(self):
        if self.snapshot.run_id != self.run_id:
            raise ValueError("the proposed snapshot must belong to this run")
        _unique_keys(self.entries, "entries")
        _unique_keys(self.on_duplicate, "duplicate entries")
        return self


class TransitionReceipt(WireModel):
    """The canonical result. An activity retry returns this instead of writing again."""

    operation_id: Reference
    applied: bool
    disposition: Literal["applied", "duplicate"]
    recorded_revision: Count
    last_sequence: Count
    snapshot: RunSnapshot
