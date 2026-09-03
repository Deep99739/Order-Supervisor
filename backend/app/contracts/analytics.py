"""What a run's numbers mean.

Every field here is defined next to the query that produces it, because the risk with
analytics is not arithmetic — it is a number whose unit quietly changes between the
database and the screen. A "5" that means five proposals must never be read as five
messages, and an event that was deferred must never be counted as one that was handled.

Three rules shape the whole shape:

* **Canonical records only.** These come from the activity log, bounded by the recorded
  cutoff the run had reached. Nothing is estimated, and nothing is inferred from a
  counter that could have drifted — `counter_checks` compares the two instead.
* **Proposed is not done.** Action outcomes stay split by disposition. There is no
  "actions taken" total that quietly includes blocked work.
* **Absent stays absent.** Token usage is whatever the provider reported. When it
  reported nothing, the field is null rather than an estimate, and no cost, saving,
  satisfaction, or success rate is derived from any of this.
"""

from typing import Annotated
from uuid import UUID

from pydantic import Field

from app.contracts.common import Count, Reference, UTCDateTime, WireModel
from app.domain.vocabulary import CloseReason, RunStatus

Breakdown = Annotated[dict[str, Count], Field(max_length=64)]


class TriggerBreakdown(WireModel):
    """Decision episodes by what caused them.

    The first three are the triggers the assignment names. A control reassessment — a
    resume, a recovery, or a changed instruction — is counted apart from them, so timer
    behaviour is never claimed from an operator's action.
    """

    start: Count = 0
    important_event: Count = 0
    scheduled_wake: Count = 0
    control_reassessment: Count = 0


class ActionOutcomes(WireModel):
    """Proposals by what actually happened to them.

    Only `committed` is a recorded effect. `blocked` was refused; `pending_review` is a
    customer draft waiting on a person. Adding them together would describe work that
    did not happen.
    """

    committed: Count = 0
    blocked: Count = 0
    pending_review: Count = 0


class TokenUsage(WireModel):
    """Provider-reported usage only.

    `calls` counts provider calls the log records; `reported_calls` counts how many of
    them came back with usage numbers. When those differ, the totals below cover part of
    the work — which is why both are here rather than a single confident number.
    """

    input_tokens: Count | None = None
    output_tokens: Count | None = None
    calls: Count = 0
    reported_calls: Count = 0


class CounterCheck(WireModel):
    """One cached counter against the same quantity derived from canonical records.

    The run's snapshot maintains counters inside the same transaction that writes the
    records, so these should agree. Publishing the comparison means a drift shows up as a
    visible disagreement rather than as a number nobody can check.
    """

    metric: Reference
    recorded: Count
    derived: Count
    agrees: bool


class RunAnalytics(WireModel):
    run_id: UUID
    order_id: Reference
    observed_at: UTCDateTime
    # Every count below describes the log up to this sequence and no further.
    through_sequence: Count
    recorded_revision: Count

    status: RunStatus
    close_reason: CloseReason | None = None
    started_at: UTCDateTime
    closed_at: UTCDateTime | None = None
    # Original start to closure, or to this observation. Continuing the history does not
    # reset it: the order's clock and the execution's clock are different things.
    duration_seconds: Count

    # Distinct business event identities that were applied, including the one creation
    # event the run records for itself.
    unique_events: Count = 0
    # Deliveries of an event that was already recorded. A retry of the same duplicate
    # command resolves to its original record and is not counted again.
    duplicate_events: Count = 0
    # Recorded without waking the agent at that point. Not ignored, and not resolved:
    # the evidence is carried until a review covers it.
    deferred_events: Count = 0
    events_by_type: Breakdown = Field(default_factory=dict)

    # Episodes that were started. Provider attempts inside one episode are not episodes.
    decision_episodes: Count = 0
    # Episodes whose conclusions were actually recorded. The difference between this and
    # `decision_episodes` is real work that was thrown away, not a rounding error: an
    # episode is discarded when the order moved under it while the model was thinking.
    completed_episodes: Count = 0
    discarded_episodes: Count = 0
    episodes_by_trigger: TriggerBreakdown = Field(default_factory=TriggerBreakdown)
    # Reasoning attempts dispatched across those episodes, including retried ones.
    provider_attempts: Count = 0
    # Closing-report calls. Reporting has its own budget and is not an order decision.
    report_attempts: Count = 0

    action_outcomes: ActionOutcomes = Field(default_factory=ActionOutcomes)
    # Committed receipts by action name. Operation-receipt audit rows are not effects
    # and never appear here.
    committed_by_action: Breakdown = Field(default_factory=dict)
    blocked_by_reason: Breakdown = Field(default_factory=dict)

    # Events the policy flagged for a person, typically an unfamiliar type.
    review_flags: Count = 0
    # Customer-draft reviews by what was recorded. A draft is not a message.
    review_outcomes: Breakdown = Field(default_factory=dict)

    open_issues: Count = 0
    # Open issues currently marked as needing a person.
    escalated_issues: Count = 0

    # Summary versions that were actually adopted. A refused refresh is counted apart.
    compactions: Count = 0
    refused_compactions: Count = 0
    # Executions that genuinely resumed. A prepared-but-abandoned rollover is not one.
    continuations: Count = 0
    prepared_continuations: Count = 0

    # Provider, persistence, and recovery incidents. These are operational problems, not
    # order outcomes: a failed review is not a failed payment.
    operational_failures: Count = 0
    failures_by_kind: Breakdown = Field(default_factory=dict)

    tokens: TokenUsage = Field(default_factory=TokenUsage)
    counter_checks: Annotated[list[CounterCheck], Field(max_length=16)] = Field(
        default_factory=list
    )
