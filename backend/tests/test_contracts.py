import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from temporalio.converter import DataConverter

from app.contracts.commands import EventCommand, InstructionCommand, ReviewCommand
from app.contracts.decision import DecisionProposal
from app.contracts.run import RunSnapshot
from app.workflows.state import WorkflowCarry

EXAMPLE = Path(__file__).resolve().parents[2] / "contracts/examples/run-snapshot.json"
COMMAND_ID = "b32ab96d-88ae-4a53-8516-b4ef863a2ee1"


async def test_snapshot_and_carry_round_trip_default_temporal_json():
    data = json.loads(EXAMPLE.read_text())
    snapshot = RunSnapshot.model_validate(data)
    assert snapshot.model_dump(mode="json") == data
    carry = WorkflowCarry(
        initial_event_id=COMMAND_ID,
        confirmed_snapshot=snapshot,
        operation_counter=12,
        decision_counter=3,
        pending_commands=[
            {
                "kind": "control",
                "command": {"command_id": COMMAND_ID, "kind": "pause"},
            }
        ],
        pending_control_intents={COMMAND_ID: "pause"},
        pending_trigger="important_event",
    )
    wire = carry.model_dump(mode="json")
    payloads = await DataConverter.default.encode([wire])
    (decoded,) = await DataConverter.default.decode(payloads, [dict])
    restored = WorkflowCarry.model_validate(decoded)
    assert restored == carry
    assert restored.confirmed_snapshot.maximum_age_at == snapshot.maximum_age_at
    assert restored.pending_commands[0].command.kind == "pause"
    assert restored.operation_counter == 12


def test_unknown_event_is_evidence_and_timestamps_normalize_to_utc():
    event = EventCommand(
        command_id=COMMAND_ID,
        event_id="courier-42",
        event_type="courier_exception",
        occurred_at="2026-09-03T08:00:00+05:30",
        payload={"message": "Keep this as evidence"},
    )
    assert event.model_dump(mode="json")["occurred_at"] == "2026-09-03T02:30:00Z"
    assert event.event_type == "courier_exception"


@pytest.mark.parametrize(
    "patch",
    [
        {"occurred_at": "2026-09-03T02:30:00"},
        {"command_id": "not-a-uuid"},
        {"event_type": "delivered", "payload": {}},
        {"event_type": "customer_message_received", "payload": {"message": " "}},
        {"event_type": "no_update_for_n_hours", "payload": {"hours": float("nan")}},
        {"event_type": "no_update_for_n_hours", "payload": {"hours": 0}},
        {"payload": {"message": "x" * 2001}},
        {"payload": {"chunks": ["界" * 1500, "界" * 1500]}},
    ],
)
def test_invalid_event_fails_before_any_signal(patch):
    value = dict(
        command_id=COMMAND_ID,
        event_id="source-1",
        event_type="custom_event",
        occurred_at="2026-09-03T02:30:00Z",
        payload={},
    )
    with pytest.raises(ValidationError):
        EventCommand.model_validate(value | patch)


def test_proposals_and_operator_commands_do_not_grant_extra_authority():
    with pytest.raises(ValidationError):
        DecisionProposal(
            rationale="Stop",
            actions=[{"action": "close_workflow", "content": "Done", "rationale": "Done"}],
        )
    with pytest.raises(ValidationError):
        DecisionProposal(rationale="Wait", sleep_for_seconds=30, sleep_until="2026-09-03T03:00:00Z")
    with pytest.raises(ValidationError):
        InstructionCommand(command_id=COMMAND_ID, operation="remove", text="New restriction")
    with pytest.raises(ValidationError):
        ReviewCommand(
            command_id=COMMAND_ID,
            draft_id="draft-1",
            content_digest="a" * 64,
            decision="approve",
            content="Replacement text",
        )
    snapshot = json.loads(EXAMPLE.read_text())
    with pytest.raises(ValidationError):
        RunSnapshot.model_validate(snapshot | {"status": "completed"})
    with pytest.raises(ValidationError):
        RunSnapshot.model_validate(snapshot | {"workflow_id": "different-order"})
