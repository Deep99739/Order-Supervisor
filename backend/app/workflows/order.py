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
    from app.contracts.decision import (
        ActionProposal,
        DecisionRequest,
        DecisionResult,
        EvidenceBundle,
        EvidenceRequest,
    )
    from app.contracts.persistence import ProposedEntry, TransitionReceipt, TransitionRequest
    from app.contracts.run import (
        ActiveInstruction,
        CommittedAction,
        ContextStamp,
        CustomerDraft,
        EvidenceReference,
        FinalOutput,
        RunSnapshot,
        WakeGuidance,
    )
    from app.domain import actions as action_registry
    from app.domain import assembly, guidance, lifecycle, memory, policy
    from app.domain import events as event_rules
    from app.domain.assembly import Assembled
    from app.domain.authorization import (
        AdmittedAction,
        Authorization,
        authorize,
        follow_up_interval,
    )
    from app.domain.digest import canonical_digest
    from app.domain.vocabulary import (
        ACTION_LEDGER,
        CLOSED_STATUS,
        CONTEXT_BUDGET_BYTES,
        CONTINUATION_EVENTS,
        CONTROL_SIGNAL,
        DEMO_CONTINUATION_EVENTS,
        EVENT_SIGNAL,
        EVIDENCE_REFERENCES,
        INSTRUCTION_SIGNAL,
        RECENT_RECORDS,
        REVIEW_SIGNAL,
        WORKFLOW_TYPE,
        ActionName,
        BlockReason,
        CloseReason,
        ControlKind,
        DecisionTrigger,
        KnownEvent,
        RunStatus,
        decision_id,
        operation_id,
    )
    from app.workflows.state import WorkflowCarry, WorkflowInput

COMMIT_ACTIVITY = "commit_transition"
DECIDE_ACTIVITY = "decide"
EVIDENCE_ACTIVITY = "load_evidence"
COMMIT_TIMEOUT = timedelta(seconds=20)
DECIDE_TIMEOUT = timedelta(seconds=45)
# Bounded so a failing provider cannot become an inference loop.
MAX_DECISION_ATTEMPTS = 2
# A burst of events can keep invalidating a review. After this many consecutive discards
# the run stops trying and asks for an operator, rather than reasoning in a hot loop.
MAX_STALE_DISCARDS = 2
MAX_LATE_DRAIN_ROUNDS = 8
HOLDS = {ControlKind.PAUSE, ControlKind.INTERRUPT, ControlKind.TERMINATE}
# A draft in either of these states occupies the run's single review slot.
LIVE_DRAFT = ("pending", "approved")

_CONTROL_DISCARD = (
    "Discarded: operator control or lifecycle intent arrived while this review was "
    "running, so its conclusions no longer apply."
)
_STALE_DRAFT = (
    "This draft was written for a situation that has since changed, so it can no longer "
    "be sent. A new assessment produces a new draft."
)


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
        self._drafts = 0
        self._stale_discards = 0
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
        carried = initial_input.get("kind") == "carry"
        if carried:
            self._restore(WorkflowCarry.model_validate(initial_input))
        else:
            data = WorkflowInput.model_validate(initial_input)
            self._snapshot = data.snapshot
            self._initial_event_id = data.initial_event_id

        if self._snapshot.status in set(CLOSED_STATUS.values()):
            return self._result("already closed")

        if carried:
            # A resumed execution records the generation it is, once, and picks up the
            # work it was already doing. It never initialises the order again.
            await self._record_generation()
        elif self._snapshot.status == RunStatus.STARTING:
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
            # 5. An approved draft is released here, not inside a decision: approval is
            #    an operator act, and it survives a pause to be revalidated on resume.
            await self._settle_draft()
            if self._closure is not None:
                return await self._finalize()
            # 6/7. Paused and recovering runs record, but do not reason.
            if self._trigger is not None and self._can_decide():
                await self._run_decision()
                continue
            # 8. Nothing is going to reason now, so keep the narrative current without a
            #    model call. A held run relies on this.
            await self._settle_memory()
            # 9. Make the waiting state visible before actually waiting.
            await self._settle_waiting_state()
            # 10. A long-lived order outgrows one Temporal execution. This is the only
            #     safe place to hand over: nothing is in flight and the record is settled.
            await self._roll_over_if_due()
            await self._wait_for_work()

    # -------------------------------------------------------------------- continuation

    def _restore(self, carry: WorkflowCarry) -> None:
        """Pick up exactly where the previous execution left off.

        Counters come back because they mint identifiers: restarting them would reuse an
        operation ID and make a replayed write look like a fresh one. Commands and
        operator intent come back because accepting work and then dropping it at an
        internal boundary the operator never asked for would be the worst kind of bug.
        """
        self._snapshot = carry.confirmed_snapshot
        self._initial_event_id = carry.initial_event_id
        self._operations = carry.operation_counter
        self._decisions = carry.decision_counter
        self._drafts = carry.draft_counter
        self._stale_discards = carry.stale_discards
        self._terminal_pending = carry.terminal_pending
        self._inbox = [
            {"kind": item.kind, "command": item.command} for item in carry.pending_commands
        ]
        self._latches = {
            key: latch.model_dump(mode="json")
            for key, latch in carry.pending_control_intents.items()
        }
        if carry.closure_latch is not None:
            self._closure = {
                "reason": carry.closure_latch.reason,
                "observed_at": carry.closure_latch.observed_at.isoformat(),
            }
        self._trigger = carry.pending_trigger
        self._trigger_detail = carry.pending_trigger_detail or ""

    def _carry(self) -> dict[str, Any]:
        latch = self._closure
        return WorkflowCarry(
            initial_event_id=self._initial_event_id,
            confirmed_snapshot=self._snapshot,
            operation_counter=self._operations,
            decision_counter=self._decisions,
            draft_counter=self._drafts,
            stale_discards=self._stale_discards,
            pending_commands=[
                {"kind": item["kind"], "command": item["command"]} for item in self._inbox
            ],
            pending_control_intents=self._latches,
            terminal_pending=self._terminal_pending,
            closure_latch=(
                {"reason": latch["reason"], "observed_at": latch["observed_at"]}
                if latch
                else None
            ),
            pending_trigger=self._trigger,
            pending_trigger_detail=self._trigger_detail or None,
        ).model_dump(mode="json")

    def _continuation_threshold(self) -> int:
        if self._snapshot.supervisor.wake_profile.mode == "demo":
            return DEMO_CONTINUATION_EVENTS
        return CONTINUATION_EVENTS

    def _continuation_due(self) -> bool:
        """History length is per execution, so this counter resets itself on rollover.

        That is the whole reason a fresh execution cannot immediately continue again.
        """
        info = workflow.info()
        return (
            info.is_continue_as_new_suggested()
            or info.get_current_history_length() >= self._continuation_threshold()
        )

    async def _record_generation(self) -> None:
        """Say, once, which execution this is. A preparation is not a rollover; an
        execution that actually resumed is."""
        document = self._document()
        document["execution_generation"] += 1
        document["counters"]["continuations"] += 1
        generation = document["execution_generation"]
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="continuation",
                    disposition="applied",
                    explanation=(
                        f"History continued as generation {generation}. The order, its "
                        "deadline, and everything pending carried over unchanged."
                    ),
                    dedupe_key=f"operation:generation/{generation}",
                    dedupe_digest=canonical_digest(
                        {"run": str(self._snapshot.run_id), "generation": generation}
                    ),
                    details={
                        "execution_generation": generation,
                        "pending_commands": len(self._inbox),
                        "next_wake_at": document["next_wake_at"],
                        "status": document["status"],
                    },
                )
            ],
        )

    async def _roll_over_if_due(self) -> None:
        """Hand this order to a fresh Temporal execution, losing nothing.

        Ineligible unless everything has settled: no closure to run, no queued command,
        no operator intent waiting to be applied. Those are all cheap to wait for, and
        the alternative is carrying work across a boundary in an ambiguous state.
        """
        if self._closure is not None or self._inbox or self._latches:
            return
        if self._snapshot.status in {RunStatus.EVALUATING, RunStatus.FINALIZING}:
            return
        if not self._continuation_due():
            return

        generation = self._snapshot.execution_generation + 1
        await self._commit(
            self._snapshot,
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="continuation",
                    disposition="recorded",
                    explanation=(
                        f"Preparing to continue this order's history as generation "
                        f"{generation}; supervision itself is unaffected."
                    ),
                    details={
                        "stage": "prepared",
                        "next_generation": generation,
                        "history_events": workflow.info().get_current_history_length(),
                    },
                )
            ],
        )

        # Anything that arrived while that write was in flight is settled here, so it
        # crosses the boundary as carried work rather than as a lost signal. A terminal
        # command that won in the meantime closes the run instead of continuing it.
        await self._drain_inbox()
        self._observe_age()
        if self._closure is not None:
            return
        await workflow.wait_condition(workflow.all_handlers_finished)
        workflow.continue_as_new(self._carry())

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
        started = RunSnapshot.model_validate(document)
        # One deterministic summary so the run is never described by an empty string.
        opening = memory.deterministic(
            started, now=now, reason="Opening summary rendered from the order's own record."
        )

        await self._commit(
            self._with_summary(started, opening),
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
        now = workflow.now()
        outcome = event_rules.interpret(self._snapshot, command, now=now, evidence=evidence)
        carried = self._snapshot.wake_guidance
        verdict = policy.classify(
            outcome,
            self._snapshot,
            command.event_type,
            hints=guidance.active(self._snapshot, now=now),
            guidance_version=carried.version if carried else None,
        )

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
        candidate = RunSnapshot.model_validate(document)

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
                        "guidance_hint": verdict.guidance_hint,
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
            candidate = RunSnapshot.model_validate(document)
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
        """Approve or reject one exact draft.

        Approval belongs to specific content in a specific context. It is not blanket
        permission to write to the customer, and it does not release an effect by itself
        — that happens in `_settle_draft`, where the run's current state gets a say.
        """
        try:
            command = ReviewCommand.model_validate(raw)
        except ValidationError:
            await self._record_only(
                "review", "rejected", "The review command could not be validated.", raw
            )
            return

        draft = self._snapshot.pending_review
        if draft is None or draft.draft_id != command.draft_id:
            await self._record_review(
                command, "rejected", "No customer draft with that identity is waiting for review."
            )
            return
        if draft.status != "pending":
            await self._record_review(
                command, "rejected", f"That draft is already {draft.status}, so it cannot change."
            )
            return
        if draft.content_digest != command.content_digest:
            # Approval cannot carry a replacement body; an edit needs a new draft.
            await self._record_review(
                command,
                "conflict",
                "The approved content is not the draft that is waiting; an edited message "
                "needs a new draft.",
            )
            return
        if self._draft_stale(draft):
            document = self._document()
            document["pending_review"] = draft.model_dump(mode="json") | {"status": "outdated"}
            await self._commit(
                RunSnapshot.model_validate(document),
                [self._review_entry(command, "conflict", _STALE_DRAFT)],
            )
            return

        decision = command.decision
        document = self._document()
        document["pending_review"] = draft.model_dump(mode="json") | {
            "status": "approved" if decision == "approve" else "rejected",
            "review_command_id": str(command.command_id),
        }
        explanation = (
            "Approved by an operator. The message is recorded once the run's current state "
            "still supports it."
            if decision == "approve"
            else "Rejected by an operator. No customer message is recorded and the review "
            "slot is free."
        )
        await self._commit(
            RunSnapshot.model_validate(document),
            [self._review_entry(command, "applied", explanation)],
        )

    def _draft_stale(self, draft: CustomerDraft) -> bool:
        """A draft speaks for the situation it was written in, and no other.

        Deliberately `context_version` and not `control_epoch`: what invalidates a
        message to a customer is the facts or the instructions changing under it. An
        operator pausing and resuming is not a reason to void the approval that same
        operator gave — otherwise approving during a hold could never mean anything.
        """
        return draft.context.context_version != self._snapshot.context_version

    def _review_entry(
        self, command: ReviewCommand, disposition: str, explanation: str
    ) -> ProposedEntry:
        envelope = command.model_dump(mode="json")
        return ProposedEntry(
            entry_id=workflow.uuid4(),
            kind="review",
            disposition=disposition,
            explanation=explanation[:500],
            command_id=command.command_id,
            # A replayed approval resolves to this same record instead of acting twice.
            dedupe_key=f"command:{command.command_id}",
            dedupe_digest=canonical_digest(envelope),
            details=envelope,
        )

    async def _record_review(
        self, command: ReviewCommand, disposition: str, explanation: str
    ) -> None:
        await self._commit(self._snapshot, [self._review_entry(command, disposition, explanation)])

    # -------------------------------------------------------------- customer draft release

    async def _settle_draft(self) -> None:
        """Decide what becomes of the current draft now that the situation is known."""
        draft = self._snapshot.pending_review
        if draft is None or draft.status not in LIVE_DRAFT:
            return
        if not self._draft_stale(draft):
            if draft.status == "approved" and self._can_act():
                await self._commit_approved_draft(draft)
            return

        # Material facts, a changed instruction, or an operator boundary moved underneath
        # it. Neither a pending nor an approved draft survives that.
        document = self._document()
        document["pending_review"] = draft.model_dump(mode="json") | {"status": "outdated"}
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="review",
                    disposition="conflict",
                    explanation=_STALE_DRAFT,
                    decision_id=draft.decision_id,
                    action_id=draft.action_id,
                    details={"draft_id": draft.draft_id, "previous_status": draft.status},
                )
            ],
        )

    async def _commit_approved_draft(self, draft: CustomerDraft) -> None:
        """Record the customer message and spend the approval in one transaction."""
        now = workflow.now()
        entry_id = workflow.uuid4()
        sequence = self._snapshot.last_sequence + 1
        proposal = ActionProposal(
            action=ActionName.MESSAGE_CUSTOMER,
            content=draft.content,
            issue_id=draft.issue_id,
            rationale="Approved by an operator before the customer was contacted.",
        )
        document = self._document()
        document["pending_review"] = None
        document["context_version"] += 1
        self._absorb_action(
            document,
            AdmittedAction(
                ordinal=1,
                action_id=draft.action_id,
                audience=action_registry.audience_of(ActionName.MESSAGE_CUSTOMER),
                proposal=proposal,
                review="approved",
            ),
            entry_id=entry_id,
            sequence=sequence,
            now=now,
        )
        await self._commit(
            RunSnapshot.model_validate(document),
            [
                ProposedEntry(
                    entry_id=entry_id,
                    kind="action",
                    disposition="committed",
                    explanation="Customer message recorded after operator approval.",
                    decision_id=draft.decision_id,
                    action_id=draft.action_id,
                    dedupe_key=f"action:{draft.action_id}",
                    dedupe_digest=canonical_digest(
                        {"action_id": draft.action_id, "content": draft.content}
                    ),
                    details=action_registry.receipt_details(proposal, review="approved")
                    | {"draft_id": draft.draft_id, "content_digest": draft.content_digest},
                ),
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="review",
                    disposition="applied",
                    explanation="Approval consumed. It cannot authorise a second message.",
                    action_id=draft.action_id,
                    details={
                        "draft_id": draft.draft_id,
                        "review_command_id": str(draft.review_command_id),
                    },
                ),
            ],
        )

    # ------------------------------------------------------------------------ decisions

    def _can_decide(self) -> bool:
        return (
            self._closure is None
            and not self._latches
            and self._snapshot.status
            not in {RunStatus.PAUSED, RunStatus.AWAITING_RECOVERY, RunStatus.FINALIZING}
        )

    def _can_act(self) -> bool:
        """Whether a business effect may be committed at this instant.

        Stricter than `_can_decide`: an operator hold that has been taken but not yet
        recorded still stops an effect, and so does a pause already on its way in.
        """
        return self._can_decide() and self._snapshot.pending_control is None

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
        evidence = await self._assemble_evidence()
        oversize = assembly.over_budget(
            DecisionRequest(
                decision_id=reference,
                trigger=trigger,
                attempt=1,
                context=stamp,
                snapshot=self._snapshot,
                trigger_detail=detail or "Reviewing the order.",
                considered=evidence.considered,
                unconsidered=evidence.unconsidered,
            ).model_dump(mode="json")
        )
        if oversize is not None:
            await self._enter_recovery(
                reference,
                0,
                f"The context needed for this review is {oversize // 1024} KiB, beyond the "
                f"{CONTEXT_BUDGET_BYTES // 1024} KiB this run will send. Nothing was "
                "truncated. Supersede an instruction or resolve an open concern, then "
                "resume.",
                next_action="consolidate_context",
            )
            return

        result, attempts, failure = await self._attempt_decision(
            reference, trigger, detail, stamp, evidence
        )

        if result is None and failure is None:
            await self._discard_decision(reference, attempts, _CONTROL_DISCARD)
            return
        if result is None:
            await self._enter_recovery(reference, attempts, failure or "The decision failed.")
            return

        # Everything that queued up while the model was thinking is settled *before* its
        # conclusions are judged. This is the point where a plan can turn out to be about
        # a situation that no longer exists.
        await self._drain_inbox()
        self._observe_age()

        verdict = authorize(
            self._snapshot,
            result.proposal,
            stamp,
            reference,
            now=workflow.now(),
            closing=self._closure is not None,
            held=not self._can_act(),
        )
        if verdict.stale:
            await self._discard_stale(reference, trigger, detail, attempts, verdict)
            return
        self._stale_discards = 0
        if verdict.global_block is not None:
            await self._discard_decision(reference, attempts, verdict.explanation, verdict=verdict)
            return
        await self._record_decision(reference, trigger, result, attempts, stamp, verdict)

    async def _discard_stale(
        self, reference: str, trigger: Any, detail: str, attempts: int, verdict: Authorization
    ) -> None:
        """Let go of a review the world moved on from, and bound how often that repeats."""
        self._stale_discards += 1
        await self._discard_decision(reference, attempts, verdict.explanation, verdict=verdict)
        if self._stale_discards >= MAX_STALE_DISCARDS:
            self._stale_discards = 0
            await self._enter_recovery(
                reference,
                0,
                f"{MAX_STALE_DISCARDS} consecutive reviews were invalidated by newly arriving "
                "context. Supervision is holding rather than reasoning in a loop; resume once "
                "the order has settled.",
            )
            return
        # Reassess under the trigger that asked for this, unless drained work already set one.
        if self._trigger is None and self._can_decide():
            self._trigger = trigger
            self._trigger_detail = detail

    async def _assemble_evidence(self) -> Assembled:
        """Read back the inputs this decision should see, by sequence.

        The references live in the snapshot; the entries themselves live in the log. This
        is the one read the workflow makes, and it never writes.
        """
        plan = assembly.plan(self._snapshot)
        if not plan.sequences:
            return Assembled()
        raw = await workflow.execute_activity(
            EVIDENCE_ACTIVITY,
            EvidenceRequest(
                run_id=self._snapshot.run_id, sequences=list(plan.sequences)
            ).model_dump(mode="json"),
            start_to_close_timeout=COMMIT_TIMEOUT,
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        return assembly.split(plan, EvidenceBundle.model_validate(raw).records)

    async def _attempt_decision(
        self,
        reference: str,
        trigger: Any,
        detail: str,
        stamp: ContextStamp,
        evidence: Assembled,
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
                considered=evidence.considered,
                unconsidered=evidence.unconsidered,
            )
            handle = workflow.start_activity(
                DECIDE_ACTIVITY,
                request.model_dump(mode="json"),
                start_to_close_timeout=DECIDE_TIMEOUT,
                # The episode owns the retry budget, so the SDK does not multiply it.
                retry_policy=RetryPolicy(maximum_attempts=1),
                # Reading has no side effect to unwind, so an obsolete review is simply
                # let go of rather than chased. Cancellation stays best effort.
                cancellation_type=workflow.ActivityCancellationType.ABANDON,
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
        # Detach from the abandoned review; whatever it eventually returns is ignored.
        handle.cancel()
        try:
            await handle
        except BaseException:  # noqa: BLE001 - an abandoned result carries no authority
            pass
        return False

    async def _record_decision(
        self,
        reference: str,
        trigger: Any,
        result: DecisionResult,
        attempts: int,
        stamp: ContextStamp,
        verdict: Authorization,
    ) -> None:
        """Commit the whole episode — conclusions, admitted effects, refusals, and the
        next review — as one transaction, so the record can never show half of it."""
        proposal = result.proposal
        now = workflow.now()
        schedule = lifecycle.effective_wake(proposal, self._snapshot, now=now)

        document = self._document()
        document["status"] = str(RunStatus.SLEEPING)
        document["next_wake_at"] = schedule.deadline.isoformat()
        document["wake_reason"] = proposal.rationale[:500]
        document["counters"]["decisions"] += 1
        document["counters"]["model_attempts"] += attempts
        document["last_decision_through_sequence"] = stamp.evidence_through_sequence
        # Only evidence up to this decision's own input cutoff counts as considered.
        # Anything that arrived while the model was thinking is still waiting for a look.
        document["deferred_evidence"] = assembly.still_pending(
            self._snapshot, stamp.evidence_through_sequence
        )
        if verdict.commits_anything:
            # A recorded effect and a waiting draft are both material context for the
            # next decision, so anything prepared against the old one is now stale.
            document["context_version"] += 1

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
                    "usage": result.usage.model_dump(mode="json") if result.usage else None,
                    "completion_recommendation": proposal.completion_recommendation,
                    "admitted": len(verdict.admitted),
                    "blocked": len(verdict.blocked),
                    "stage": "completed",
                },
            )
        ]

        def sequence_of_next_entry() -> int:
            return self._snapshot.last_sequence + len(entries) + 1

        for admitted in verdict.admitted:
            entry_id = workflow.uuid4()
            self._absorb_action(
                document, admitted, entry_id=entry_id, sequence=sequence_of_next_entry(), now=now
            )
            entries.append(
                ProposedEntry(
                    entry_id=entry_id,
                    kind="action",
                    disposition="committed",
                    explanation=admitted.proposal.rationale,
                    decision_id=reference,
                    action_id=admitted.action_id,
                    # A replay of this transition resolves to the original receipt.
                    dedupe_key=f"action:{admitted.action_id}",
                    dedupe_digest=canonical_digest(
                        {
                            "action_id": admitted.action_id,
                            "content": admitted.proposal.content,
                        }
                    ),
                    details=action_registry.receipt_details(
                        admitted.proposal, review=admitted.review
                    ),
                )
            )

        for refused in verdict.blocked:
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="action",
                    disposition="blocked",
                    explanation=refused.explanation,
                    decision_id=reference,
                    action_id=refused.action_id,
                    details={
                        "action": str(refused.action),
                        "reason": str(refused.reason),
                        "executed": False,
                    },
                )
            )

        if verdict.draft is not None:
            request = verdict.draft
            self._drafts += 1
            draft = CustomerDraft(
                draft_id=f"{self._snapshot.run_id}/draft/{self._drafts}",
                decision_id=reference,
                action_id=request.action_id,
                issue_id=request.proposal.issue_id,
                content=request.proposal.content,
                content_digest=canonical_digest({"content": request.proposal.content}),
                reason=request.reason,
                context=ContextStamp(
                    context_version=document["context_version"],
                    control_epoch=document["control_epoch"],
                    # The last entry this transition will write. Binding the draft to the
                    # state *after* this commit is what stops the episode's own internal
                    # receipts from ageing out the draft it created alongside them.
                    evidence_through_sequence=self._snapshot.last_sequence + len(entries) + 2,
                ),
                status="pending",
            )
            document["pending_review"] = draft.model_dump(mode="json")
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="action",
                    disposition="pending_review",
                    explanation=request.reason,
                    decision_id=reference,
                    action_id=request.action_id,
                    details={
                        "action": str(ActionName.MESSAGE_CUSTOMER),
                        "reason": str(BlockReason.APPROVAL_REQUIRED),
                        "draft_id": draft.draft_id,
                        "content": draft.content,
                        "content_digest": draft.content_digest,
                        "issue_id": draft.issue_id,
                        "executed": False,
                    },
                )
            )

        self._absorb_guidance(document, proposal, reference, stamp, entries, now=now)
        candidate = self._absorb_memory(
            RunSnapshot.model_validate(document), proposal, reference, stamp, entries, now=now
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

    def _absorb_guidance(
        self,
        document: dict[str, Any],
        proposal: Any,
        reference: str,
        stamp: ContextStamp,
        entries: list[ProposedEntry],
        *,
        now: Any,
    ) -> None:
        """Adopt the hints that survive validation, and say why the others did not.

        A refused hint never costs the run its existing guidance or its next deadline, so
        each one is judged on its own and recorded on its own.
        """
        offered = getattr(proposal, "wake_guidance", None)
        if offered is None:
            return
        review = guidance.check(offered, self._snapshot, stamp, now=now)
        version = (self._snapshot.wake_guidance.version + 1) if self._snapshot.wake_guidance else 1

        for refusal in review.refused:
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="policy",
                    disposition="rejected",
                    explanation=refusal.explanation[:500],
                    decision_id=reference,
                    details={
                        "guidance_hint": refusal.hint.kind,
                        "reason": refusal.reason,
                        "issue_id": refusal.hint.issue_id,
                        "event_type": refusal.hint.event_type,
                    },
                )
            )
        if not review.usable:
            return

        adopted = WakeGuidance(
            version=version,
            context=ContextStamp(
                context_version=document["context_version"],
                control_epoch=document["control_epoch"],
                evidence_through_sequence=stamp.evidence_through_sequence,
            ),
            hints=list(review.accepted),
            source_decision_id=reference,
        )
        document["wake_guidance"] = adopted.model_dump(mode="json")
        entries.append(
            ProposedEntry(
                entry_id=workflow.uuid4(),
                kind="policy",
                disposition="recorded",
                explanation=(
                    f"Wake guidance v{version} adopted: "
                    + "; ".join(
                        f"{hint.kind}"
                        + (f" for {hint.issue_id}" if hint.issue_id else "")
                        + (f" on {hint.event_type}" if hint.event_type else "")
                        for hint in review.accepted
                    )
                )[:500],
                decision_id=reference,
                details={
                    "guidance_version": version,
                    "hints": [hint.model_dump(mode="json") for hint in review.accepted],
                },
            )
        )

    def _absorb_memory(
        self,
        candidate: RunSnapshot,
        proposal: Any,
        reference: str,
        stamp: ContextStamp,
        entries: list[ProposedEntry],
        *,
        now: Any,
    ) -> RunSnapshot:
        """Take the agent's summary if it is usable, and fall back rather than fail.

        A refused proposal costs nothing: the previous valid summary is still there and
        the deterministic renderer still works, which is exactly why an unusable summary
        is rejected outright instead of being sliced to fit.
        """
        refresh = getattr(proposal, "memory_refresh", None)
        if refresh is not None:
            outcome = memory.from_proposal(
                self._snapshot,
                refresh,
                input_cutoff=stamp.evidence_through_sequence,
                decision_reference=reference,
                now=now,
            )
            if isinstance(outcome, memory.Compaction):
                entries.append(self._memory_entry(outcome, decision_reference=reference))
                return self._with_summary(candidate, outcome)
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="memory",
                    disposition="rejected",
                    explanation=outcome.explanation[:500],
                    decision_id=reference,
                    details={"reason": outcome.reason},
                )
            )

        if not memory.refresh_due(self._snapshot):
            return candidate
        behind = memory.records_since_summary(self._snapshot)
        fallback = memory.deterministic(
            candidate,
            now=now,
            reason=f"{behind} record(s) had accumulated past the previous summary cutoff; "
            "rendered from the order's own record.",
            through=self._snapshot.last_sequence + len(entries) + 1,
        )
        entries.append(self._memory_entry(fallback, decision_reference=reference))
        return self._with_summary(candidate, fallback)

    def _absorb_action(
        self,
        document: dict[str, Any],
        admitted: AdmittedAction,
        *,
        entry_id: Any,
        sequence: int,
        now: Any,
    ) -> None:
        """Fold one about-to-be-committed action into the candidate snapshot.

        The receipt points at the entry this transition is about to write, which is why
        the workflow names entries itself rather than letting the database assign them.
        """
        document["counters"]["committed_actions"] += 1
        ledger = document["committed_actions"] + [
            CommittedAction(
                action_id=admitted.action_id,
                action=admitted.action,
                content=admitted.proposal.content,
                receipt=EvidenceReference(sequence=sequence, activity_id=entry_id),
                recorded_at=now,
            ).model_dump(mode="json")
        ]
        document["committed_actions"] = ledger[-ACTION_LEDGER:]

        issue_id = admitted.proposal.issue_id
        if issue_id is None:
            return
        follow_up = now + follow_up_interval(self._snapshot)
        for issue in document["facts"]["open_issues"]:
            if issue["issue_id"] != issue_id:
                continue
            # One entry per audience: the question is "have we already told them", not
            # "how many times", and the snapshot stays bounded either way.
            contacts = [item for item in issue["contacts"] if item["audience"] != admitted.audience]
            contacts.append(
                {
                    "audience": str(admitted.audience),
                    "action_id": admitted.action_id,
                    "evidence_sequence": max(
                        (item["sequence"] for item in issue["evidence"]), default=0
                    ),
                    "context_version": document["context_version"],
                    "contacted_at": now.isoformat(),
                    "follow_up_at": follow_up.isoformat(),
                }
            )
            issue["contacts"] = contacts
            issue["last_action_id"] = admitted.action_id
            issue["follow_up_at"] = follow_up.isoformat()
            return

    async def _discard_decision(
        self,
        reference: str,
        attempts: int,
        explanation: str,
        *,
        verdict: Authorization | None = None,
    ) -> None:
        """Record a review whose conclusions were never authorised, and why."""
        document = self._document()
        document["counters"]["model_attempts"] += attempts
        entries = [
            ProposedEntry(
                entry_id=workflow.uuid4(),
                kind="decision",
                disposition="rejected",
                explanation=explanation[:500],
                decision_id=reference,
                details={
                    "stage": "discarded",
                    "attempts": attempts,
                    "reason": str(verdict.global_block) if verdict else None,
                },
            )
        ]
        if verdict is not None:
            # Each refused proposal is named individually: an operator needs to see what
            # the agent wanted to do, not only that something was stopped.
            entries.extend(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="action",
                    disposition="blocked",
                    explanation=refused.explanation,
                    decision_id=reference,
                    action_id=refused.action_id,
                    details={
                        "action": str(refused.action),
                        "reason": str(refused.reason),
                        "executed": False,
                    },
                )
                for refused in verdict.blocked
            )
        await self._commit(RunSnapshot.model_validate(document), entries)

    async def _enter_recovery(
        self,
        reference: str,
        attempts: int,
        failure: str,
        *,
        next_action: str = "retry_decision",
    ) -> None:
        document = self._document()
        document["status"] = str(RunStatus.AWAITING_RECOVERY)
        document["counters"]["model_attempts"] += attempts
        document["recovery"] = {
            "reason": failure[:500],
            "next_action": next_action,
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
        # An unspent draft or approval does not survive closure, and the report says so
        # rather than leaving the customer's side of it unexplained.
        draft = self._snapshot.pending_review
        abandoned = draft if draft is not None and draft.status in LIVE_DRAFT else None

        # Freeze the report cutoff. No new business work is authorized from here.
        finalizing = self._document()
        finalizing["status"] = str(RunStatus.FINALIZING)
        finalizing["pending_control"] = None
        finalizing["pending_review"] = None
        entries = [
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
        ]
        if abandoned is not None:
            entries.append(
                ProposedEntry(
                    entry_id=workflow.uuid4(),
                    kind="review",
                    disposition="too_late",
                    explanation=(
                        f"The run closed while this draft was {abandoned.status}; no customer "
                        "message was recorded for it."
                    ),
                    action_id=abandoned.action_id,
                    details={"draft_id": abandoned.draft_id, "status": abandoned.status},
                )
            )
        await self._commit(RunSnapshot.model_validate(finalizing), entries)

        final = _final_output(self._snapshot, reason, now, abandoned)
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

    # ---------------------------------------------------------------------------- memory

    def _with_summary(self, candidate: RunSnapshot, compaction: Any) -> RunSnapshot:
        document = candidate.model_dump(mode="json")
        document["memory"] = compaction.summary.model_dump(mode="json")
        document["counters"]["compactions"] += 1
        return RunSnapshot.model_validate(document)

    def _memory_entry(self, compaction: Any, *, decision_reference: str | None = None):
        """What a compaction preserved, in numbers an operator can check."""
        return ProposedEntry(
            entry_id=workflow.uuid4(),
            kind="memory",
            disposition="recorded",
            explanation=compaction.reason[:500],
            decision_id=decision_reference,
            details={
                "summary_version": compaction.summary.summary_version,
                "provenance": compaction.summary.provenance,
                "covered_from": compaction.covered_from,
                "covered_through": compaction.summary.summary_through_sequence,
                "before_chars": compaction.before_chars,
                "after_chars": compaction.after_chars,
                # Compaction shortens the narrative and nothing else. These stay whole.
                "instructions_retained": len(self._snapshot.instructions),
                "open_issues_retained": len(self._snapshot.facts.open_issues),
            },
        )

    async def _settle_memory(self) -> None:
        """Keep the narrative current without spending a model call to do it.

        This is the path a held run uses. Nothing here reads or writes anything the
        agent owns; it re-renders from confirmed state and moves the cutoff.
        """
        if not memory.refresh_due(self._snapshot):
            return
        behind = memory.records_since_summary(self._snapshot)
        compaction = memory.deterministic(
            self._snapshot,
            now=workflow.now(),
            reason=f"{behind} record(s) accumulated past the previous summary cutoff.",
            # It covers the entry that records it, so a compaction is not itself
            # unsummarised work pushing towards the next one.
            through=self._snapshot.last_sequence + 1,
        )
        await self._commit(
            self._with_summary(self._snapshot, compaction), [self._memory_entry(compaction)]
        )

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


def _final_output(
    snapshot: RunSnapshot,
    reason: CloseReason,
    now: Any,
    abandoned: CustomerDraft | None = None,
) -> FinalOutput:
    """A factual closing record built only from what was actually recorded.

    Every action listed here has a receipt. Nothing the agent merely proposed appears,
    and a concern that was never settled is reported as still open.
    """
    facts = snapshot.facts
    counters = snapshot.counters
    ended = {
        CloseReason.DELIVERED: "Delivery was recorded.",
        CloseReason.MANUALLY_TERMINATED: "An operator ended supervision.",
        CloseReason.MAXIMUM_AGE_REACHED: "The order reached its original maximum age.",
    }[reason]
    unresolved = list(facts.open_issues)
    actions = list(snapshot.committed_actions)

    summary = (
        f"{ended} Payment is {facts.payment} and shipment is {facts.shipment}. "
        f"{len(actions)} simulated action(s) were recorded and "
        f"{len(unresolved)} concern(s) remain unresolved."
    )
    learnings = [
        f"{counters.unique_events} order event(s) were admitted across "
        f"{counters.decisions} review episode(s).",
        f"{counters.deferred_events} event(s) were recorded without waking the agent.",
    ]
    if counters.duplicate_events:
        learnings.append(f"{counters.duplicate_events} duplicate delivery(ies) were ignored.")
    if counters.committed_actions:
        audiences = sorted({str(action_registry.audience_of(item.action)) for item in actions})
        learnings.append(
            f"{counters.committed_actions} action(s) were recorded, reaching: "
            f"{', '.join(audiences)}."
        )
    else:
        learnings.append("No business action was needed or authorised during this run.")
    if unresolved:
        learnings.append(
            "Unresolved at closure: " + ", ".join(issue.issue_id for issue in unresolved)
        )

    feedback = ["Every recorded action is a simulation; nothing was sent outside this system."]
    if unresolved:
        feedback.append("The unresolved concerns above need human follow-up.")
    contacted = [issue for issue in unresolved if issue.contacts]
    if contacted:
        feedback.append(
            "Already chased without resolution: "
            + ", ".join(issue.issue_id for issue in contacted)
            + ". A different audience or a person may be needed."
        )
    if abandoned is not None:
        feedback.append(
            f"A customer draft ({abandoned.draft_id}) was still {abandoned.status} when the "
            "run closed, so the customer was never written to about it."
        )

    return FinalOutput(
        close_reason=reason,
        closed_at=now,
        facts=facts,
        summary=summary[:2000],
        important_actions=actions,
        unresolved_issues=unresolved,
        learnings=[item[:500] for item in learnings][:10],
        feedback=[item[:500] for item in feedback][:10],
        narrative_provenance="factual_fallback",
        narrative_limitation=(
            "These are counts and facts from the record. A model-written closing "
            "narrative arrives with the reporting work."
        ),
        evidence_through_sequence=snapshot.last_sequence,
    )
