"""Public request and response shapes owned by the HTTP layer.

`SupervisorConfig`, `RunSnapshot`, and `ActivityRecord` remain the shared contracts.
These models carry the identifiers, paging, and acknowledgement information that only
the API needs, so the frozen per-run configuration is not widened with row metadata.
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.contracts.common import (
    Count,
    JsonObject,
    PositiveInt,
    Reference,
    ShortText,
    UTCDateTime,
    WireModel,
)
from app.contracts.run import ActivityRecord, OrderFacts, RunSnapshot
from app.contracts.supervisor import SupervisorConfig, WakeProfile
from app.domain.vocabulary import ActionName, CloseReason, ControlKind, RunStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Instructions = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]
# A readable backend label. Provider credentials belong in process configuration only.
ModelLabel = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,59}$")
]
SECRET_MARKERS = ("sk-", "sk_", "api_key", "apikey", "secret", "bearer", "token")

CONFIG_FIELDS = (
    "name",
    "base_instructions",
    "allowed_actions",
    "wake_profile",
    "maximum_age_seconds",
    "customer_review_default",
    "escalate_shipment_delays",
    "prioritize_speed",
    "model_label",
)


class SupervisorDraft(WireModel):
    """An editable configuration. Identity and version are assigned by the API."""

    name: Name
    base_instructions: Instructions
    allowed_actions: Annotated[list[ActionName], Field(min_length=1, max_length=5)]
    wake_profile: WakeProfile = Field(default_factory=WakeProfile)
    # Omitted means the profile's own default, including the shorter demo age.
    maximum_age_seconds: Annotated[int, Field(strict=True, ge=60, le=2592000)] | None = None
    customer_review_default: bool = False
    escalate_shipment_delays: bool = False
    prioritize_speed: bool = False
    model_label: ModelLabel | None = None

    @field_validator("model_label")
    @classmethod
    def label_is_not_a_credential(cls, value: str | None) -> str | None:
        # A guard against an accidental paste, not a secret scanner.
        if value and any(marker in value.casefold() for marker in SECRET_MARKERS):
            raise ValueError("model_label is a readable name; keep credentials in backend .env")
        return value

    def to_config(self, identity: UUID, version: int) -> SupervisorConfig:
        values = {field: getattr(self, field) for field in CONFIG_FIELDS}
        if values["maximum_age_seconds"] is None:
            del values["maximum_age_seconds"]
        return SupervisorConfig(id=identity, version=version, **values)


class SupervisorUpdate(SupervisorDraft):
    """A new version for future runs. Active runs keep their frozen snapshot."""

    expected_version: PositiveInt


class SupervisorRecord(WireModel):
    config: SupervisorConfig
    is_preset: bool
    created_at: UTCDateTime
    updated_at: UTCDateTime


class SupervisorList(WireModel):
    supervisors: list[SupervisorRecord]


class RunCreated(WireModel):
    """Reservation is settled; the Temporal start may still need a retry."""

    command_id: UUID
    run_id: UUID
    order_id: Name
    workflow_id: Reference
    status: RunStatus
    start: Literal["started", "retry_required"]
    start_detail: ShortText | None = None


class RunListItem(WireModel):
    run_id: UUID
    order_id: Name
    supervisor_name: Name
    initial_context: JsonObject
    status: RunStatus
    pending_control: ControlKind | None = None
    close_reason: CloseReason | None = None
    facts: OrderFacts
    next_wake_at: UTCDateTime | None = None
    updated_at: UTCDateTime
    closed_at: UTCDateTime | None = None


class RunPage(WireModel):
    runs: list[RunListItem]
    next_cursor: Reference | None = None
    observed_at: UTCDateTime


class RunView(WireModel):
    snapshot: RunSnapshot
    observed_at: UTCDateTime


class ActivityPage(WireModel):
    """Records ascend by sequence. `earlier_cursor` loads the previous page."""

    records: list[ActivityRecord]
    earlier_cursor: Count | None = None
    through_sequence: Count
    last_sequence: Count
    observed_at: UTCDateTime
