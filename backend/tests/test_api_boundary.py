"""T01 — what the HTTP boundary accepts, refuses, and never claims.

The recurring risk is a response that sounds like success. Acceptance means Temporal took
the signal; it never means the event was applied or an action was taken. An unfamiliar but
well-formed event is evidence for later handling, not an error.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.presets import PRESETS, REVIEW_FIRST_ID, STANDARD_ID
from app.storage.runs import get_run
from tests.conftest import FakeTemporal, close_run

NOW = datetime.now(UTC).isoformat().replace("+00:00", "Z")


def event(**overrides) -> dict:
    return {
        "command_id": str(uuid4()),
        "event_id": f"src-{uuid4().hex[:8]}",
        "event_type": "payment_confirmed",
        "occurred_at": NOW,
        "payload": {"payment_reference": "PAY-1"},
    } | overrides


async def make_run(api, order_id: str = "ORD-BOUNDARY-1") -> str:
    response = await api.post(
        "/api/runs",
        json={
            "command_id": str(uuid4()),
            "supervisor_id": str(STANDARD_ID),
            "order_id": order_id,
            "initial_context": {"description": "Boundary fixture"},
        },
    )
    assert response.status_code == 201
    return response.json()["run_id"]


@pytest.mark.parametrize(
    "invalid",
    [
        {"occurred_at": "2026-09-03T02:30:00"},
        {"command_id": "not-a-uuid"},
        {"event_type": "delivered", "payload": {}},
        {"event_type": "customer_message_received", "payload": {"message": "  "}},
        {"event_type": "no_update_for_n_hours", "payload": {"hours": 0}},
        {"event_type": "Payment_Confirmed"},
        {"payload": {"note": "x" * 2001}},
        {"payload": {"bulk": ["y" * 1500] * 8}},
    ],
)
async def test_invalid_event_envelopes_fail_before_any_signal(api, temporal, invalid):
    run_id = await make_run(api)
    response = await api.post(f"/api/runs/{run_id}/events", json=event(**invalid))

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
    assert response.json()["field_details"]
    assert temporal.signals == []


async def test_a_valid_unfamiliar_event_is_accepted_as_evidence(api, temporal: FakeTemporal):
    run_id = await make_run(api)
    body = event(event_type="warehouse_exception", payload={"bay": "B12", "note": "Unfamiliar"})
    response = await api.post(f"/api/runs/{run_id}/events", json=body)

    assert response.status_code == 202
    acknowledgement = response.json()
    assert acknowledgement["acceptance"] == "accepted"
    assert acknowledgement["processing"] == "pending"
    assert acknowledgement["command_id"] == body["command_id"]

    workflow_id, signal, payload = temporal.signals[0]
    assert signal == "event"
    assert workflow_id == f"order-supervisor/{run_id}"
    assert payload["event_type"] == "warehouse_exception"


async def test_acceptance_is_not_evidence_of_application(api):
    run_id = await make_run(api)
    await api.post(f"/api/runs/{run_id}/events", json=event())

    # Nothing was applied: the recorded view and history are untouched by acceptance.
    view = (await api.get(f"/api/runs/{run_id}")).json()["snapshot"]
    assert view["facts"]["payment"] == "unknown"
    assert view["recorded_revision"] == 1
    history = (await api.get(f"/api/runs/{run_id}/activity")).json()
    assert [record["kind"] for record in history["records"]] == ["run_reserved"]


async def test_sending_an_event_never_creates_a_run(api, temporal):
    missing = await api.post(f"/api/runs/{uuid4()}/events", json=event())
    assert missing.status_code == 404
    assert missing.json()["code"] == "run_not_found"
    assert temporal.signals == []


async def test_a_closed_run_refuses_commands(api, pool, temporal):
    run_id = await make_run(api, "ORD-BOUNDARY-CLOSED")
    snapshot = await get_run(pool, UUID(run_id))
    assert snapshot is not None
    await close_run(pool, snapshot)
    response = await api.post(f"/api/runs/{run_id}/events", json=event())

    assert response.status_code == 409
    assert response.json()["code"] == "run_closed"
    assert temporal.signals == []


async def test_an_unconfirmed_signal_is_not_reported_as_accepted(api, temporal):
    run_id = await make_run(api, "ORD-BOUNDARY-503")
    temporal.signal_outcome = "unavailable"
    response = await api.post(f"/api/runs/{run_id}/events", json=event())

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "temporal_unavailable"
    assert body["retryable"] is True
    assert body["command_id"] and body["run_id"] == run_id


async def test_a_run_without_a_live_execution_reports_a_conflict(api, temporal):
    run_id = await make_run(api, "ORD-BOUNDARY-404")
    temporal.signal_outcome = "missing"
    response = await api.post(f"/api/runs/{run_id}/events", json=event())

    assert response.status_code == 409
    assert response.json()["code"] == "run_not_accepting_commands"
    assert response.json()["retryable"] is False


async def test_interrupt_and_pause_are_the_same_hold(api, temporal: FakeTemporal):
    run_id = await make_run(api, "ORD-BOUNDARY-CONTROL")
    for path, kind in (("interrupt", "interrupt"), ("pause", "pause")):
        response = await api.post(
            f"/api/runs/{run_id}/{path}",
            json={"command_id": str(uuid4()), "kind": kind, "reason": "Operator hold"},
        )
        assert response.status_code == 202

    assert [payload["kind"] for _, _, payload in temporal.signals] == ["pause", "pause"]

    mismatched = await api.post(
        f"/api/runs/{run_id}/resume", json={"command_id": str(uuid4()), "kind": "terminate"}
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["code"] == "control_kind_mismatch"


async def test_instruction_shapes_and_review_targets_are_checked(api, temporal):
    run_id = await make_run(api, "ORD-BOUNDARY-INSTRUCTION")
    removal_with_text = await api.post(
        f"/api/runs/{run_id}/instructions",
        json={
            "command_id": str(uuid4()),
            "operation": "remove",
            "instruction_id": str(uuid4()),
            "text": "Sneak in a new restriction",
        },
    )
    assert removal_with_text.status_code == 422
    assert temporal.signals == []

    accepted = await api.post(
        f"/api/runs/{run_id}/instructions",
        json={
            "command_id": str(uuid4()),
            "operation": "add",
            "text": "Do not contact the customer without human review.",
            "policy_changes": {"require_customer_review": True},
        },
    )
    assert accepted.status_code == 202

    # A draft identity carries slashes like every other generated identifier, so the
    # route has to accept them rather than 404 on a real draft.
    draft_id = f"{run_id}/draft/1"
    wrong_draft = await api.post(
        f"/api/runs/{run_id}/reviews/{draft_id}",
        json={
            "command_id": str(uuid4()),
            "draft_id": f"{run_id}/draft/2",
            "content_digest": "a" * 64,
            "decision": "approve",
        },
    )
    assert wrong_draft.status_code == 422
    assert wrong_draft.json()["code"] == "review_target_mismatch"

    matching = await api.post(
        f"/api/runs/{run_id}/reviews/{draft_id}",
        json={
            "command_id": str(uuid4()),
            "draft_id": draft_id,
            "content_digest": "a" * 64,
            "decision": "approve",
        },
    )
    assert matching.status_code == 202, "a slash-bearing draft identity must be routable"


async def test_supervisor_configurations_are_listed_versioned_and_guarded(api):
    listed = (await api.get("/api/supervisors")).json()["supervisors"]
    assert {record["config"]["name"] for record in listed} == {
        preset.name for preset in PRESETS
    }
    assert all(record["is_preset"] for record in listed)

    review_first = (await api.get(f"/api/supervisors/{REVIEW_FIRST_ID}")).json()
    assert review_first["config"]["customer_review_default"] is True

    draft = {
        "name": "Weekend cover",
        "base_instructions": "Escalate only what blocks the order.",
        "allowed_actions": ["create_internal_note"],
    }
    created = await api.post("/api/supervisors", json=draft)
    assert created.status_code == 201
    identity = created.json()["config"]["id"]
    assert created.json()["config"]["version"] == 1
    assert created.json()["is_preset"] is False

    updated = await api.patch(
        f"/api/supervisors/{identity}", json=draft | {"expected_version": 1}
    )
    assert updated.status_code == 200
    assert updated.json()["config"]["version"] == 2

    stale = await api.patch(f"/api/supervisors/{identity}", json=draft | {"expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["code"] == "supervisor_version_conflict"


async def test_a_model_label_cannot_smuggle_a_credential(api):
    response = await api.post(
        "/api/supervisors",
        json={
            "name": "Leaky",
            "base_instructions": "Watch the order.",
            "allowed_actions": ["create_internal_note"],
            "model_label": "sk-abcdef0123456789",
        },
    )
    assert response.status_code == 422
    assert "model_label" in response.json()["field_details"]
