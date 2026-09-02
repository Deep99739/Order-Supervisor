"""The start interface for one order's supervisor.

This phase owns reservation, the stable Workflow ID, and command transport. The
deterministic lifecycle — initialization, event interpretation, the wake policy, decision
triggers, durable deadlines, controls, and closure — belongs to the next phase and is
deliberately absent. This execution accepts its immutable input and registers incoming
commands so the transport path is genuinely exercised. It takes no business action,
starts no decision, and writes nothing to the database.
"""

from typing import Any

from temporalio import workflow

from app.domain.vocabulary import (
    CONTROL_SIGNAL,
    EVENT_SIGNAL,
    INSTRUCTION_SIGNAL,
    REVIEW_SIGNAL,
    WORKFLOW_TYPE,
)


@workflow.defn(name=WORKFLOW_TYPE)
class OrderSupervisor:
    def __init__(self) -> None:
        self._initial_input: dict[str, Any] = {}
        self._inbox: list[dict[str, Any]] = []

    @workflow.run
    async def run(self, initial_input: dict[str, Any]) -> None:
        self._initial_input = initial_input
        # The supervisor stays alive for the order. The control loop arrives next phase;
        # until then this execution only holds the run open so commands are accepted.
        await workflow.wait_condition(lambda: False)

    @workflow.signal(name=EVENT_SIGNAL)
    def event(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "event", "command": command})

    @workflow.signal(name=INSTRUCTION_SIGNAL)
    def instruction(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "instruction", "command": command})

    @workflow.signal(name=CONTROL_SIGNAL)
    def control(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "control", "command": command})

    @workflow.signal(name=REVIEW_SIGNAL)
    def review(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "review", "command": command})

    @workflow.query(name="pending_commands")
    def pending_commands(self) -> list[dict[str, Any]]:
        """Diagnostic only. Recorded application state comes from PostgreSQL."""
        return list(self._inbox)
