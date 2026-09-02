import json
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.contracts.commands import EventCommand, InstructionCommand
from app.contracts.decision import ActionProposal, DecisionProposal
from app.contracts.run import ActivityRecord
from app.contracts.supervisor import SupervisorConfig, WakeProfile
from app.domain.vocabulary import (
    ACTIVITY_DETAILS_BYTES,
    DEMO_MAXIMUM_AGE_SECONDS,
    INSTRUCTION_CHARS,
    MESSAGE_CHARS,
    PAYLOAD_BYTES,
    ActionName,
)

IDENTITY = UUID("11111111-1111-4111-8111-111111111111")
TIMESTAMP = "2026-09-03T00:00:00Z"


def activity(details, *, kind="event"):
    return ActivityRecord(
        id=IDENTITY,
        run_id=IDENTITY,
        sequence=1,
        kind=kind,
        recorded_at=TIMESTAMP,
        disposition="recorded",
        explanation="Recorded source details",
        details=details,
    )


def supervisor(**overrides):
    return SupervisorConfig(
        id=IDENTITY,
        version=1,
        name="Example supervisor",
        base_instructions="Follow up on recorded order progress.",
        allowed_actions=list(ActionName),
        **overrides,
    )


def test_near_limit_event_retains_its_full_envelope_in_history():
    payload = {f"field_{index}": "x" * MESSAGE_CHARS for index in range(4)}
    event = EventCommand(
        command_id=IDENTITY,
        event_id="external-event-reference",
        event_type="unfamiliar_event",
        occurred_at=TIMESTAMP,
        payload=payload,
    )
    envelope = event.model_dump(mode="json")
    assert len(json.dumps(payload, separators=(",", ":")).encode()) <= PAYLOAD_BYTES
    assert len(json.dumps(envelope, separators=(",", ":")).encode()) > PAYLOAD_BYTES
    assert activity(envelope).details == envelope

    with pytest.raises(ValidationError, match="8 KiB"):
        EventCommand.model_validate({**envelope, "payload": {**payload, "extra": "x" * 200}})


def test_full_length_instruction_can_be_recorded_without_truncation():
    command = InstructionCommand(
        command_id=IDENTITY,
        operation="add",
        text="x" * INSTRUCTION_CHARS,
    )
    record = activity(command.model_dump(mode="json"), kind="instruction")
    assert record.details["text"] == command.text


def test_complete_action_batch_fits_record_details():
    proposal = DecisionProposal(
        rationale="Recorded reasoning",
        actions=[
            ActionProposal(action=action, content="x" * MESSAGE_CHARS, rationale="Follow up")
            for action in ActionName
        ],
        sleep_for_seconds=300,
    )
    details = proposal.model_dump(mode="json")
    assert len(json.dumps(details).encode()) > PAYLOAD_BYTES
    assert activity(details, kind="decision").details == details


def test_record_details_keep_explicit_size_and_string_bounds():
    oversized = {
        str(index): "x" * INSTRUCTION_CHARS
        for index in range(ACTIVITY_DETAILS_BYTES // INSTRUCTION_CHARS + 1)
    }
    with pytest.raises(ValidationError, match="128 KiB"):
        activity(oversized)
    with pytest.raises(ValidationError, match="4000 characters"):
        activity({"text": "x" * (INSTRUCTION_CHARS + 1)})


def test_record_details_keep_the_nesting_bound():
    nested = {"value": "evidence"}
    for _ in range(16):
        nested = {"child": nested}
    with pytest.raises(ValidationError, match="16 levels"):
        activity(nested)


def test_demo_defaults_are_selected_without_overriding_explicit_values():
    profile = WakeProfile(mode="demo")
    assert (profile.minimum_seconds, profile.default_seconds, profile.maximum_seconds) == (
        10,
        20,
        60,
    )
    assert supervisor(wake_profile={"mode": "demo"}).maximum_age_seconds == (
        DEMO_MAXIMUM_AGE_SECONDS
    )
    assert supervisor(wake_profile=profile).maximum_age_seconds == DEMO_MAXIMUM_AGE_SECONDS
    explicit = supervisor(
        wake_profile={"mode": "demo", "default_seconds": 40}, maximum_age_seconds=120
    )
    assert explicit.wake_profile.default_seconds == 40
    assert explicit.maximum_age_seconds == 120
    assert supervisor().wake_profile.default_seconds == 300
    assert supervisor().maximum_age_seconds == 86400
    with pytest.raises(ValidationError):
        supervisor(wake_profile={"mode": "demo", "default_seconds": 61})
    with pytest.raises(ValidationError):
        supervisor(wake_profile={"mode": "demo"}, maximum_age_seconds=1801)
