"""One durable supervisor per order.

This is the runtime the whole product hangs off. It stays alive while the order does,
admits commands as signals, records what they mean, and asks the agent to reason only on
the three triggers the brief names: the start, an important event, and a due review.

Three boundaries are load bearing here:

* **Admission is not application.** A signal lands in an inbox. It becomes real only when
  a canonical database receipt says so, and the workflow promotes nothing before that.
* **A decision is not an authority.** The agent proposes actions and timing. Lifecycle,
  operator holds, and closure stay with this loop. `completion_recommendation` is advice.
* **Nothing crosses a control boundary silently.** Intent latched during inference kills
  the result it would otherwise have authorised.

Business actions and the real provider belong to the next phase; a proposal is recorded
here as `proposed` and executes nothing.
"""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from pydantic import ValidationError

    from app.contracts.commands import (
        ControlCommand,
        EventCommand,
        InstructionCommand,
        ReviewCommand,
    )
    from app.contracts.decision import DecisionRequest, DecisionResult
    from app.contracts.persistence import ProposedEntry, TransitionReceipt, TransitionRequest
    from app.contracts.run import (
        ActiveInstruction,
        ContextStamp,
        EvidenceReference,
        FinalOutput,
        RunSnapshot,
    )
    from app.domain import events as event_rules
    from app.domain import lifecycle, memory, policy
    from app.domain.digest import canonical_digest
    from app.domain.vocabulary import (
        CLOSED_STATUS,
        CONTROL_SIGNAL,
        EVENT_SIGNAL,
        EVIDENCE_REFERENCES,
        INSTRUCTION_SIGNAL,
        RECENT_RECORDS,
        REVIEW_SIGNAL,
        WORKFLOW_TYPE,
        CloseReason,
        ControlKind,
        DecisionTrigger,
        KnownEvent,
        RunStatus,
        decision_id,
        operation_id,
    )
    from app.workflows.state import WorkflowInput

COMMIT_ACTIVITY = "commit_transition"
DECIDE_ACTIVITY = "decide"
COMMIT_TIMEOUT = timedelta(seconds=20)
DECIDE_TIMEOUT = timedelta(seconds=45)
# Bounded so a failing provider cannot become an inference loop.
MAX_DECISION_ATTEMPTS = 2
MAX_LATE_DRAIN_ROUNDS = 8
HOLDS = {ControlKind.PAUSE, ControlKind.INTERRUPT, ControlKind.TERMINATE}


@workflow.defn(name=WORKFLOW_TYPE)
class OrderSupervisor:
    def __init__(self) -> None:
        self._snapshot: RunSnapshot | None = None
        self._initial_event_id: Any = None
        self._inbox: list[dict[str, Any]] = []
        # Provisional holds keyed by command identity, retired when admission settles.
        self._latches: dict[str, dict[str, Any]] = {}
        self._terminal_pending = False
        self._operations = 0
        self._decisions = 0
        self._pending_operation: TransitionRequest | None = None
        self._closure: dict[str, Any] | None = None
        self._trigger: DecisionTrigger | None = None
        self._trigger_detail = ""

    # ---------------------------------------------------------------- signals and queries

    @workflow.signal(name=EVENT_SIGNAL)
    def event(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "event", "command": command})
        if command.get("event_type") == KnownEvent.DELIVERED:
            self._terminal_pending = True

    @workflow.signal(name=INSTRUCTION_SIGNAL)
    def instruction(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "instruction", "command": command})

    @workflow.signal(name=CONTROL_SIGNAL)
    def control(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "control", "command": command})
        kind = command.get("kind")
        if kind in HOLDS:
            # A conservative authorization hold, taken immediately so a decision already
            # in flight cannot slip past this operator boundary.
            self._latches[str(command.get("command_id"))] = {
                "kind": kind,
                "during_effect": self._pending_operation is not None,
            }

    @workflow.signal(name=REVIEW_SIGNAL)
    def review(self, command: dict[str, Any]) -> None:
        self._inbox.append({"kind": "review", "command": command})

    @workflow.query(name="pending_commands")
    def pending_commands(self) -> list[dict[str, Any]]:
        """Diagnostic only. The recorded application view comes from PostgreSQL."""
        return list(self._inbox)

    @workflow.query(name="state")
    def state(self) -> dict[str, Any]:
        """Diagnostic view of live workflow state, distinct from the recorded view."""
        snapshot = self._snapshot
        return {
            "status": str(snapshot.status) if snapshot else None,
            "next_wake_at": (
                snapshot.next_wake_at.isoformat() if snapshot and snapshot.next_wake_at else None
            ),
            "recorded_revision": snapshot.recorded_revision if snapshot else None,
            "pending_commands": len(self._inbox),
            "holds": len(self._latches),
            "closure": self._closure["reason"] if self._closure else None,
            "pending_trigger": str(self._trigger) if self._trigger else None,
        }

    # ------------------------------------------------------------------------ the loop

    @workflow.run
    async def run(self, initial_input: dict[str, Any]) -> dict[str, Any]:
        data = WorkflowInput.model_validate(initial_input)
        self._snapshot = data.snapshot
        self._initial_event_id = data.initial_event_id
        if self._snapshot.status in set(CLOSED_STATUS.values()):
            return self._result("already closed")

        if self._snapshot.status == RunStatus.STARTING:
            await self._initialize()

        while True:
            # 1. A lifecycle deadline outranks everything waiting behind it.
            self._observe_age()
            # 2/3. Settle admitted work before authorizing anything new.
            await self._drain_inbox()
            self._observe_age()
            # 4. Closing runs the controlled finalization path and nothing else.
            if self._closure is not None:
                return await self._finalize()
            # 5/6/7. Paused and recovering runs record, but do not reason.
            if self._trigger is not None and self._can_decide():
                await self._run_decision()
                continue
            # 9. Make the waiting state visible before actually waiting.
            await self._settle_waiting_state()
            await self._wait_for_work()

    # ------------------------------------------------------------------- initialization

    async def _initialize(self) -> None:
        """Record the canonical creation evidence once and schedule the start decision."""
        now = workflow.now()
        entry_id = workflow.uuid4()
        evidence = EvidenceReference(
            sequence=self._snapshot.last_sequence + 1, activity_id=entry_id
        )
        document = self._document()
        document["status"] = str(RunStatus.EVALUATING)
        document["facts"]["last_relevant_progress_at"] = self._snapshot.started_at.isoformat()
        document["counters"]["unique_events"] += 1
        document["recent_evidence"] = [evidence.model_dump(mode="json")]
        candidate = self._with_memory(RunSnapshot.model_validate(document))

        await self._commit(
            candidate,
            [
                ProposedEntry(
                    entry_id=entry_id,
                    kind="event",
                    disposition="applied",
                    explanation=f"Supervision started for order {self._snapshot.order_id}.",
                    occurred_at=self._snapshot.started_at,
                    event_id=str(self._initial_event_id),
                    dedupe_key=f"event:{self._initial_event_id}",
                    dedupe_digest=canonical_digest(
                        {
                            "event_type": str(KnownEvent.ORDER_CREATED),
                            "run": str(self._snapshot.run_id),
                        }
                    ),
                    details={
                        "event_type": str(KnownEvent.ORDER_CREATED),
                        "initial_context": self._snapshot.initial_context,
                    },
                )
            ],
        )
        self._trigger = DecisionTrigger.START
        self._trigger_detail = "Supervision started; reviewing the order for the first time."
        del now

    # ------------------------------------------------------------------------- draining

    async def _drain_inbox(self) -> None:
        while self._inbox and self._closure is None:
            item = self._inbox.pop(0)
            handler = {
                "event": self._apply_event,
                "instruction": self._apply_instruction,
                "control": self._apply_control,
                "review": self._apply_review,
            }[item["kind"]]
            await handler(item["command"])

    async def _apply_event(self, raw: dict[str, Any]) -> None:
        try:
            command = EventCommand.model_validate(raw)
        except ValidationError:
            await self._record_only(
                "event", "rejected", "The event envelope could not be validated.", raw
            )
            return

        event_entry_id = workflow.uuid4()
        evidence = EvidenceReference(
            sequence=self._snapshot.last_sequence + 1, activity_id=event_entry_id
        )
        outcome = event_rules.interpret(
            self._snapshot, command, now=workflow.now(), evidence=evidence
        )
        verdict = policy.classify(outcome, self._snapshot, command.event_type)

        document = self._document()
        document["facts"] = outcome.facts.model_dump(mode="json")
        document["counters"]["unique_events"] += 1
        if verdict.outcome == "deferred":
            document["counters"]["deferred_events"] += 1
            document["deferred_evidence"] = (
                document["deferred_evidence"] + [evidence.model_dump(mode="json")]
            )[-EVIDENCE_REFERENCES:]
        if outcome.material:
            document["context_version"] += 1
        document["recent_evidence"] = (
            document["recent_evidence"] + [evidence.model_dump(mode="json")]
        )[-RECENT_RECORDS:]
        document["unresolved_evidence"] = _unresolved_evidence(outcome.facts)
        candidate = self._with_memory(RunSnapshot.model_validate(document))

        envelope = command.model_dump(mode="json")
        command_digest = canonical_digest(envelope)
        receipt = await self._commit(
            candidate,
            [
                ProposedEntry(
                    entry_id=event_entry_id,
                    kind="event",
                    disposition="applied",
                    explanation=outcome.explanation,
                    occurred_at=command.occurred_at,
                    command_id=command.command_id,
                    event_id=command.event_id,
                    dedupe_key=f"event:{command.event_id}",
                    dedupe_digest=canonical_digest(envelope["payload"]),
                    details=envelope,
                ),
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="policy",
                    disposition=verdict.outcome,
                    explanation=verdict.reason,
                    command_id=command.command_id,
                    event_id=command.event_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=command_digest,
                    details={
                        "wake": verdict.wake,
                        "review_required": verdict.review_required,
                        "guidance_version": verdict.guidance_version,
                    },
                ),
            ],
            on_duplicate=[
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="event",
                    disposition="duplicate",
                    explanation="This order event was already recorded; the delivery is a repeat.",
                    command_id=command.command_id,
                    event_id=command.event_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=command_digest,
                    details={"event_type": command.event_type},
                )
            ],
        )

        # A repeat delivery reapplies nothing: no facts, no triggers, no closure.
        if not receipt.applied:
            return
        if outcome.terminal:
            self._latch_closure(CloseReason.DELIVERED)
        elif verdict.wake:
            self._trigger = DecisionTrigger.IMPORTANT_EVENT
            self._trigger_detail = f"{command.event_type}: {verdict.reason}"[:500]

    async def _apply_instruction(self, raw: dict[str, Any]) -> None:
        try:
            command = InstructionCommand.model_validate(raw)
        except ValidationError:
            await self._record_only(
                "instruction", "rejected", "The instruction could not be validated.", raw
            )
            return

        existing = list(self._snapshot.instructions)
        if command.operation == "add":
            existing.append(
                ActiveInstruction(
                    instruction_id=workflow.uuid4(),
                    text=command.text,
                    added_at=workflow.now(),
                    source_command_id=command.command_id,
                    policy_changes=command.policy_changes,
                )
            )
        else:
            known = [item for item in existing if item.instruction_id == command.instruction_id]
            if not known:
                await self._record_only(
                    "instruction",
                    "rejected",
                    "That instruction is not active, so it cannot be superseded or removed.",
                    raw,
                )
                return
            existing = [item for item in existing if item.instruction_id != command.instruction_id]
            if command.operation == "supersede":
                existing.append(
                    ActiveInstruction(
                        instruction_id=workflow.uuid4(),
                        text=command.text,
                        added_at=workflow.now(),
                        source_command_id=command.command_id,
                        policy_changes=command.policy_changes,
                    )
                )

        document = self._document()
        document["instructions"] = [item.model_dump(mode="json") for item in existing]
        document["context_version"] += 1
        try:
            candidate = self._with_memory(RunSnapshot.model_validate(document))
        except ValidationError:
            # Standing instructions are never silently truncated to make room.
            await self._record_only(
                "instruction",
                "capacity_exceeded",
                "Active instructions would exceed the supported text capacity; "
                "supersede or remove one first.",
                raw,
            )
            return

        receipt = await self._commit(
            candidate,
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="instruction",
                    disposition="applied",
                    explanation=f"Instruction {command.operation} applied.",
                    command_id=command.command_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=canonical_digest(command.model_dump(mode="json")),
                    details=command.model_dump(mode="json"),
                )
            ],
        )
        if receipt.applied and self._can_decide():
            self._trigger = DecisionTrigger.CONTROL_REASSESSMENT
            self._trigger_detail = "A standing instruction changed; reassessing the order."

    async def _apply_control(self, raw: dict[str, Any]) -> None:
        try:
            command = ControlCommand.model_validate(raw)
        except ValidationError:
            await self._record_only(
                "control", "rejected", "The control command could not be validated.", raw
            )
            return

        latch = self._latches.pop(str(command.command_id), None)
        kind = command.kind
        if kind in {ControlKind.PAUSE, ControlKind.INTERRUPT}:
            await self._apply_hold(command, latch)
        elif kind == ControlKind.RESUME:
            await self._apply_resume(command)
        else:
            await self._apply_terminate(command)

    async def _apply_hold(self, command: ControlCommand, latch: dict[str, Any] | None) -> None:
        if self._snapshot.status == RunStatus.PAUSED:
            await self._record_only(
                "control", "rejected", "The run is already paused.", command.model_dump(mode="json")
            )
            return

        if latch is not None and latch.get("during_effect"):
            # The hold arrived after a transaction had been dispatched. Show "Pausing"
            # until that transaction's receipt settled; only then is the run paused.
            pausing = self._document()
            pausing["pending_control"] = str(ControlKind.PAUSE)
            await self._commit(
                RunSnapshot.model_validate(pausing),
                [
                    ProposedEntry(
                        entry_id=workflow.uuid4(),
                        kind="control",
                        disposition="applied",
                        explanation="Pausing: an already-started transaction is finishing.",
                        command_id=command.command_id,
                        details={"kind": str(ControlKind.PAUSE), "stage": "pausing"},
                    )
                ],
            )

        document = self._document()
        document["status"] = str(RunStatus.PAUSED)
        document["pending_control"] = None
        document["control_epoch"] += 1
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="control",
                    disposition="applied",
                    explanation="Paused. Events are still recorded; agent work is suspended."
                    + (f" Reason: {command.reason}" if command.reason else ""),
                    command_id=command.command_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=canonical_digest(command.model_dump(mode="json")),
                    details=command.model_dump(mode="json"),
                )
            ],
        )
        # A decision prepared before this boundary is no longer authorised.
        self._trigger = None

    async def _apply_resume(self, command: ControlCommand) -> None:
        recovering = self._snapshot.status == RunStatus.AWAITING_RECOVERY
        if self._snapshot.status != RunStatus.PAUSED and not recovering:
            await self._record_only(
                "control",
                "rejected",
                "The run is not paused and has nothing to recover.",
                command.model_dump(mode="json"),
            )
            return

        document = self._document()
        document["status"] = str(RunStatus.EVALUATING)
        document["pending_control"] = None
        document["recovery"] = None
        document["control_epoch"] += 1
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="control",
                    disposition="applied",
                    explanation=(
                        "Recovery accepted; reassessing the current context."
                        if recovering
                        else "Resumed; reassessing the current context once."
                    ),
                    command_id=command.command_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=canonical_digest(command.model_dump(mode="json")),
                    details=command.model_dump(mode="json"),
                )
            ],
        )
        # One current-context assessment, not a replay of every missed timer.
        self._trigger = DecisionTrigger.CONTROL_REASSESSMENT
        self._trigger_detail = "Resumed by the operator; assessing everything accumulated since."

    async def _apply_terminate(self, command: ControlCommand) -> None:
        document = self._document()
        document["pending_control"] = None
        document["control_epoch"] += 1
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="control",
                    disposition="applied",
                    explanation="Graceful termination requested by the operator."
                    + (f" Reason: {command.reason}" if command.reason else ""),
                    command_id=command.command_id,
                    dedupe_key=f"command:{command.command_id}",
                    dedupe_digest=canonical_digest(command.model_dump(mode="json")),
                    details=command.model_dump(mode="json"),
                )
            ],
        )
        self._latch_closure(CloseReason.MANUALLY_TERMINATED)
        self._trigger = None

    async def _apply_review(self, raw: dict[str, Any]) -> None:
        try:
            command = ReviewCommand.model_validate(raw)
        except ValidationError:
            await self._record_only(
                "review", "rejected", "The review command could not be validated.", raw
            )
            return
        await self._record_only(
            "review",
            "rejected",
            "No customer draft is pending. Drafts arrive with agent decisions.",
            command.model_dump(mode="json"),
            command_id=command.command_id,
        )

    # ------------------------------------------------------------------------ decisions

    def _can_decide(self) -> bool:
        return (
            self._closure is None
            and not self._latches
            and self._snapshot.status
            not in {RunStatus.PAUSED, RunStatus.AWAITING_RECOVERY, RunStatus.FINALIZING}
        )

    async def _run_decision(self) -> None:
        trigger = self._trigger
        detail = self._trigger_detail
        self._trigger = None
        self._decisions += 1
        reference = decision_id(self._snapshot.run_id, self._decisions)

        # Evaluating and its trigger are recorded before any model work begins.
        evaluating = self._document()
        evaluating["status"] = str(RunStatus.EVALUATING)
        await self._commit(
            RunSnapshot.model_validate(evaluating),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="decision",
                    disposition="recorded",
                    explanation=f"Reviewing the order ({trigger}). {detail}"[:500],
                    decision_id=reference,
                    details={"trigger": str(trigger), "stage": "started"},
                )
            ],
        )

        stamp = ContextStamp(
            context_version=self._snapshot.context_version,
            control_epoch=self._snapshot.control_epoch,
            evidence_through_sequence=self._snapshot.last_sequence,
        )
        result, attempts, failure = await self._attempt_decision(reference, trigger, detail, stamp)

        if result is None and failure is None:
            await self._discard_decision(reference, attempts)
            return
        if result is None:
            await self._enter_recovery(reference, attempts, failure or "The decision failed.")
            return
        # A decision computed before a new control boundary cannot cross it.
        if not self._can_decide() or self._snapshot.control_epoch != stamp.control_epoch:
            await self._discard_decision(reference, attempts)
            return
        await self._record_decision(reference, trigger, result, attempts, stamp)

    async def _attempt_decision(
        self, reference: str, trigger: Any, detail: str, stamp: ContextStamp
    ) -> tuple[DecisionResult | None, int, str | None]:
        failure: str | None = None
        attempts = 0
        for attempt in range(1, MAX_DECISION_ATTEMPTS + 1):
            attempts = attempt
            request = DecisionRequest(
                decision_id=reference,
                trigger=trigger,
                attempt=attempt,
                context=stamp,
                snapshot=self._snapshot,
                trigger_detail=detail or "Reviewing the order.",
            )
            handle = workflow.start_activity(
                DECIDE_ACTIVITY,
                request.model_dump(mode="json"),
                start_to_close_timeout=DECIDE_TIMEOUT,
                # The episode owns the retry budget, so the SDK does not multiply it.
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
            completed = await self._await_decision(handle)
            if not completed:
                return None, attempts, None
            try:
                return DecisionResult.model_validate(await handle), attempts, None
            except (ActivityError, ValidationError) as error:
                failure = _readable(error)
        return None, attempts, failure

    async def _await_decision(self, handle: Any) -> bool:
        """Wait for the decision without going blind to controls or the age deadline."""
        interrupted = asyncio.ensure_future(
            workflow.wait_condition(lambda: bool(self._latches) or self._terminal_pending)
        )
        remaining = self._snapshot.maximum_age_at - workflow.now()
        expiry = asyncio.ensure_future(workflow.sleep(max(remaining, timedelta(0))))
        try:
            done, _ = await workflow.wait(
                [handle, interrupted, expiry], return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for task in (interrupted, expiry):
                task.cancel()
        if handle in done:
            return True
        # The model has no side effects, so an obsolete result can simply be dropped.
        handle.cancel()
        return False

    async def _record_decision(
        self,
        reference: str,
        trigger: Any,
        result: DecisionResult,
        attempts: int,
        stamp: ContextStamp,
    ) -> None:
        proposal = result.proposal
        schedule = lifecycle.effective_wake(proposal, self._snapshot, now=workflow.now())

        document = self._document()
        document["status"] = str(RunStatus.SLEEPING)
        document["next_wake_at"] = schedule.deadline.isoformat()
        document["wake_reason"] = proposal.rationale[:500]
        document["counters"]["decisions"] += 1
        document["counters"]["model_attempts"] += attempts
        document["last_decision_through_sequence"] = stamp.evidence_through_sequence
        # This episode considered the evidence deferred up to its cutoff.
        document["deferred_evidence"] = []
        candidate = self._with_memory(RunSnapshot.model_validate(document))

        entries = [
            ProposedEntry(
                entry_id=workflow.uuid4(),
                kind="decision",
                disposition="recorded",
                explanation=proposal.rationale[:500],
                decision_id=reference,
                details={
                    "trigger": str(trigger),
                    "provenance": result.provenance,
                    "model_label": result.model_label,
                    "attempts": attempts,
                    "completion_recommendation": proposal.completion_recommendation,
                    "stage": "completed",
                },
            )
        ]
        for ordinal, action in enumerate(proposal.actions, start=1):
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="action",
                    # Proposed, never executed: authorisation and receipts are the next phase.
                    disposition="proposed",
                    explanation=action.rationale,
                    decision_id=reference,
                    action_id=f"{reference}/action/{ordinal}",
                    details={
                        "action": str(action.action),
                        "content": action.content,
                        "issue_id": action.issue_id,
                        "executed": False,
                    },
                )
            )
        entries.append(
            ProposedEntry(
                entry_id=workflow.uuid4(),
                kind="sleep",
                disposition="recorded",
                explanation=schedule.explanation,
                decision_id=reference,
                details={
                    "next_wake_at": schedule.deadline.isoformat(),
                    "used_template_default": schedule.used_default,
                },
            )
        )
        await self._commit(candidate, entries)

    async def _discard_decision(self, reference: str, attempts: int) -> None:
        document = self._document()
        document["counters"]["model_attempts"] += attempts
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="decision",
                    disposition="rejected",
                    explanation=(
                        "Discarded: operator control or lifecycle intent arrived while this "
                        "review was running, so its conclusions no longer apply."
                    ),
                    decision_id=reference,
                    details={"stage": "discarded", "attempts": attempts},
                )
            ],
        )

    async def _enter_recovery(self, reference: str, attempts: int, failure: str) -> None:
        document = self._document()
        document["status"] = str(RunStatus.AWAITING_RECOVERY)
        document["counters"]["model_attempts"] += attempts
        document["recovery"] = {
            "reason": failure[:500],
            "next_action": "retry_decision",
        }
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="recovery",
                    disposition="failed",
                    explanation=f"The review could not complete after {attempts} attempt(s).",
                    decision_id=reference,
                    details={"reason": failure[:2000], "next_action": "retry_decision"},
                )
            ],
        )

    # -------------------------------------------------------------------------- waiting

    async def _settle_waiting_state(self) -> None:
        """Record the state and deadline before waiting, so a live timer is never invisible."""
        if self._snapshot.status != RunStatus.EVALUATING:
            return
        now = workflow.now()
        deadline = self._snapshot.next_wake_at
        if deadline is None or deadline <= now:
            schedule = lifecycle.effective_wake(None, self._snapshot, now=now)
            deadline = schedule.deadline
        document = self._document()
        document["status"] = str(RunStatus.SLEEPING)
        document["next_wake_at"] = deadline.isoformat()
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="sleep",
                    disposition="recorded",
                    explanation="Waiting for the next event or scheduled review.",
                    details={"next_wake_at": deadline.isoformat()},
                )
            ],
        )

    async def _wait_for_work(self) -> None:
        now = workflow.now()
        deadlines = [self._snapshot.maximum_age_at]
        # A paused or recovering run keeps its overdue review as evidence without
        # spinning on it; it waits for input, a terminal rule, or maximum age.
        if self._snapshot.status == RunStatus.SLEEPING and self._snapshot.next_wake_at:
            deadlines.append(self._snapshot.next_wake_at)
        delay = max(min(deadlines) - now, timedelta(0))
        try:
            await workflow.wait_condition(
                lambda: bool(self._inbox) or bool(self._latches), timeout=delay
            )
        except TimeoutError:
            pass

        due = self._snapshot.next_wake_at
        if (
            self._snapshot.status == RunStatus.SLEEPING
            and due is not None
            and workflow.now() >= due
            and self._trigger is None
        ):
            self._trigger = DecisionTrigger.SCHEDULED_WAKE
            self._trigger_detail = "The scheduled review time was reached."

    # ------------------------------------------------------------------------- closure

    def _observe_age(self) -> None:
        if self._closure is None and lifecycle.maximum_age_reached(self._snapshot, workflow.now()):
            self._latch_closure(CloseReason.MAXIMUM_AGE_REACHED)

    def _latch_closure(self, reason: CloseReason) -> None:
        """The first eligible cause in deterministic order fixes the reason."""
        if self._closure is None:
            self._closure = {"reason": reason, "observed_at": workflow.now().isoformat()}

    async def _finalize(self) -> dict[str, Any]:
        reason = CloseReason(self._closure["reason"])
        now = workflow.now()

        # Freeze the report cutoff. No new business work is authorized from here.
        finalizing = self._document()
        finalizing["status"] = str(RunStatus.FINALIZING)
        finalizing["pending_control"] = None
        finalizing["pending_review"] = None
        await self._commit(
            RunSnapshot.model_validate(finalizing),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="finalization",
                    disposition="recorded",
                    explanation=f"Closing under the {reason} rule; freezing the report facts.",
                    details={
                        "close_reason": str(reason),
                        "observed_at": self._closure["observed_at"],
                    },
                )
            ],
        )

        final = _final_output(self._snapshot, reason, now)
        closed = self._document()
        closed["status"] = str(CLOSED_STATUS[reason])
        closed["close_reason"] = str(reason)
        closed["closed_at"] = now.isoformat()
        closed["next_wake_at"] = None
        closed["wake_reason"] = None
        closed["final_output"] = final.model_dump(mode="json")
        await self._commit(
            RunSnapshot.model_validate(closed),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="finalization",
                    disposition="recorded",
                    explanation="Final record saved; supervision has ended.",
                    details={"close_reason": str(reason), "provenance": final.narrative_provenance},
                )
            ],
        )

        await self._dispose_late_commands()
        return self._result(str(reason))

    async def _dispose_late_commands(self) -> None:
        """Give known queued commands a visible disposition before the workflow returns."""
        for _ in range(MAX_LATE_DRAIN_ROUNDS):
            if not self._inbox:
                return
            pending, self._inbox = self._inbox, []
            entries = [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind=_LATE_KINDS[item["kind"]],
                    disposition="too_late",
                    explanation="Supervision had already closed when this command was admitted.",
                    details={"kind": item["kind"]},
                )
                for item in pending[: 32]
            ]
            await self._commit(self._snapshot, entries)

    def _result(self, reason: str) -> dict[str, Any]:
        return {
            "run_id": str(self._snapshot.run_id),
            "order_id": self._snapshot.order_id,
            "status": str(self._snapshot.status),
            "close_reason": reason,
        }

    # -------------------------------------------------------------------------- helpers

    def _document(self) -> dict[str, Any]:
        return self._snapshot.model_dump(mode="json")

    def _with_memory(self, candidate: RunSnapshot) -> RunSnapshot:
        text = memory.render_summary(candidate)
        if text == candidate.memory.text:
            return candidate
        document = candidate.model_dump(mode="json")
        document["memory"] = {
            "text": text,
            "summary_version": candidate.memory.summary_version + 1,
            "summary_through_sequence": candidate.last_sequence,
            "recorded_at": workflow.now().isoformat(),
        }
        document["counters"]["compactions"] += 1
        return RunSnapshot.model_validate(document)

    async def _record_only(
        self,
        kind: str,
        disposition: str,
        explanation: str,
        details: dict[str, Any],
        *,
        command_id: Any = None,
    ) -> None:
        """Record an outcome without changing the run's semantic state."""
        await self._commit(
            self._snapshot,
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind=kind,
                    disposition=disposition,
                    explanation=explanation[:500],
                    command_id=command_id,
                    details=details,
                )
            ],
        )

    async def _commit(
        self,
        candidate: RunSnapshot,
        entries: list[ProposedEntry],
        *,
        on_duplicate: list[ProposedEntry] | None = None,
    ) -> TransitionReceipt:
        self._operations += 1
        request = TransitionRequest(
            run_id=self._snapshot.run_id,
            operation_id=operation_id(self._snapshot.run_id, self._operations),
            request_digest=canonical_digest(
                {
                    "operation": self._operations,
                    "entries": [entry.model_dump(mode="json") for entry in entries],
                }
            ),
            expected_recorded_revision=self._snapshot.recorded_revision,
            snapshot=candidate,
            entries=entries,
            on_duplicate=on_duplicate or [],
        )
        self._pending_operation = request
        try:
            raw = await workflow.execute_activity(
                COMMIT_ACTIVITY,
                request.model_dump(mode="json"),
                start_to_close_timeout=COMMIT_TIMEOUT,
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
        finally:
            self._pending_operation = None
        receipt = TransitionReceipt.model_validate(raw)
        # Confirmed state moves only when a canonical receipt says it did.
        self._snapshot = receipt.snapshot
        return receipt


_LATE_KINDS = {
    "event": "event",
    "instruction": "instruction",
    "control": "control",
    "review": "review",
}


def _unresolved_evidence(facts: Any) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    references: list[dict[str, Any]] = []
    for issue in facts.open_issues:
        for reference in issue.evidence:
            key = (reference.sequence, str(reference.activity_id))
            if key in seen:
                continue
            seen.add(key)
            references.append(reference.model_dump(mode="json"))
    return references[:EVIDENCE_REFERENCES]


def _readable(error: Exception) -> str:
    cause = getattr(error, "cause", None)
    return str(cause) if cause else str(error)


def _final_output(snapshot: RunSnapshot, reason: CloseReason, now: Any) -> FinalOutput:
    """A factual closing record. Narrative learnings arrive with the agent phase."""
    facts = snapshot.facts
    counters = snapshot.counters
    ended = {
        CloseReason.DELIVERED: "Delivery was recorded.",
        CloseReason.MANUALLY_TERMINATED: "An operator ended supervision.",
        CloseReason.MAXIMUM_AGE_REACHED: "The order reached its original maximum age.",
    }[reason]
    unresolved = list(facts.open_issues)

    summary = (
        f"{ended} Payment is {facts.payment} and shipment is {facts.shipment}. "
        f"{len(unresolved)} concern(s) remain unresolved."
    )
    learnings = [
        f"{counters.unique_events} order event(s) were admitted across "
        f"{counters.decisions} review episode(s).",
        f"{counters.deferred_events} event(s) were recorded without waking the agent.",
    ]
    if counters.duplicate_events:
        learnings.append(f"{counters.duplicate_events} duplicate delivery(ies) were ignored.")
    if unresolved:
        learnings.append(
            "Unresolved at closure: " + ", ".join(issue.issue_id for issue in unresolved)
        )

    feedback = [
        "No simulated business actions were executed; action authorisation and receipts "
        "arrive with the agent implementation."
    ]
    if unresolved:
        feedback.append("The unresolved concerns above need human follow-up.")

    return FinalOutput(
        close_reason=reason,
        closed_at=now,
        facts=facts,
        summary=summary[:2000],
        important_actions=[],
        unresolved_issues=unresolved,
        learnings=[item[:500] for item in learnings][:10],
        feedback=[item[:500] for item in feedback][:10],
        narrative_provenance="factual_fallback",
        narrative_limitation=(
            "These are counts and facts from the record. Narrative learnings and "
            "recommendations arrive with the agent implementation."
        ),
        evidence_through_sequence=snapshot.last_sequence,
    )
