"""Deriving a run's numbers from its canonical records.

Two queries, both bounded by the run's recorded cutoff. The first groups every entry by
the fields that carry its meaning; the second sums the numeric details that only some
entries have. Nothing is read from the snapshot's cached counters except to compare
against them — if the two ever disagree, the answer says so rather than picking one.

The grouping is deliberately done in SQL and the interpretation in Python: the database
is good at counting, and the definitions belong where a reader can check them against
`app/contracts/analytics.py`.
"""

from datetime import UTC, datetime

import asyncpg

from app.contracts.analytics import (
    ActionOutcomes,
    CounterCheck,
    RunAnalytics,
    TokenUsage,
    TriggerBreakdown,
)
from app.contracts.run import RunSnapshot

# One row per meaningful combination. `details` keys are extracted here so the grouping
# happens in the database rather than by walking every row in Python.
GROUPED = """
SELECT
    kind,
    disposition,
    details ->> 'trigger'    AS trigger,
    details ->> 'stage'      AS stage,
    details ->> 'action'     AS action,
    details ->> 'reason'     AS reason,
    details ->> 'event_type' AS event_type,
    count(*)                                  AS total,
    count(DISTINCT event_id)                  AS distinct_events
FROM activity_log
WHERE run_id = $1 AND sequence <= $2
GROUP BY 1, 2, 3, 4, 5, 6, 7
"""

# `attempts` is written once per finished episode and carries that episode's whole
# reasoning budget, so summing it counts dispatches rather than episodes. The outer casts
# matter: a sum over bigint comes back as numeric, which is not an integer count.
TOTALS = """
SELECT
    coalesce(sum((details ->> 'attempts')::bigint) FILTER (WHERE kind = 'decision'), 0)::bigint
        AS provider_attempts,
    coalesce(sum((details -> 'usage' ->> 'input_tokens')::bigint), 0)::bigint  AS input_tokens,
    coalesce(sum((details -> 'usage' ->> 'output_tokens')::bigint), 0)::bigint AS output_tokens,
    count(*) FILTER (WHERE jsonb_typeof(details -> 'usage') = 'object') AS reported_calls,
    count(*) FILTER (
        WHERE (kind = 'decision' AND details ? 'attempts')
           OR (kind = 'finalization' AND details ->> 'stage' = 'narrative')
    ) AS provider_calls
FROM activity_log
WHERE run_id = $1 AND sequence <= $2
"""

# Which activity kinds count as an operational incident rather than an order outcome.
FAILURE_KINDS = {"recovery"}


def _bump(target: dict[str, int], key: str | None, amount: int) -> None:
    if key:
        target[key] = target.get(key, 0) + amount


async def read_analytics(pool: asyncpg.Pool, snapshot: RunSnapshot) -> RunAnalytics:
    """Aggregate one run's activity log, bounded by the cutoff its snapshot reached."""
    cutoff = snapshot.last_sequence
    rows = await pool.fetch(GROUPED, snapshot.run_id, cutoff)
    totals = await pool.fetchrow(TOTALS, snapshot.run_id, cutoff)
    observed_at = datetime.now(UTC)

    unique_events = 0
    duplicate_events = 0
    deferred_events = 0
    review_flags = 0
    compactions = 0
    refused_compactions = 0
    continuations = 0
    prepared_continuations = 0
    report_attempts = 0
    episodes = 0
    completed = 0
    discarded = 0
    failures = 0
    outcomes = {"committed": 0, "blocked": 0, "pending_review": 0}
    triggers = {
        "start": 0,
        "important_event": 0,
        "scheduled_wake": 0,
        "control_reassessment": 0,
    }
    events_by_type: dict[str, int] = {}
    committed_by_action: dict[str, int] = {}
    blocked_by_reason: dict[str, int] = {}
    review_outcomes: dict[str, int] = {}
    failures_by_kind: dict[str, int] = {}

    for row in rows:
        kind = row["kind"]
        disposition = row["disposition"]
        total = row["total"]

        if kind == "event":
            if disposition == "applied":
                # Identity, not row count: a redelivery reuses the same event_id.
                unique_events += row["distinct_events"]
                _bump(events_by_type, row["event_type"], total)
            elif disposition == "duplicate":
                duplicate_events += total

        elif kind == "policy":
            if disposition == "deferred":
                deferred_events += total
            elif disposition == "review_required":
                review_flags += total

        elif kind == "decision":
            stage = row["stage"]
            if stage == "started":
                episodes += total
                if row["trigger"] in triggers:
                    triggers[row["trigger"]] += total
            elif stage == "completed":
                completed += total
            elif stage == "discarded":
                discarded += total
            if disposition == "failed":
                failures += total
                _bump(failures_by_kind, "review could not complete", total)

        elif kind == "action":
            if disposition in outcomes:
                outcomes[disposition] += total
            if disposition == "committed":
                _bump(committed_by_action, row["action"], total)
            elif disposition in ("blocked", "pending_review"):
                _bump(blocked_by_reason, row["reason"], total)

        elif kind == "review":
            _bump(review_outcomes, disposition, total)

        elif kind == "memory":
            if disposition == "recorded":
                compactions += total
            elif disposition == "rejected":
                refused_compactions += total

        elif kind == "continuation":
            if disposition == "applied":
                continuations += total
            else:
                prepared_continuations += total

        elif kind == "finalization" and row["stage"] == "narrative":
            report_attempts += total

        if kind in FAILURE_KINDS:
            failures += total
            _bump(failures_by_kind, kind, total)

    reported_calls = totals["reported_calls"]
    closed_at = snapshot.closed_at
    duration = (closed_at or observed_at) - snapshot.started_at

    derived = {
        "unique_events": unique_events,
        "duplicate_events": duplicate_events,
        "deferred_events": deferred_events,
        # The counter means episodes whose conclusions were recorded, which is not the
        # same quantity as episodes that started. Compared like with like.
        "decisions": completed,
        "model_attempts": totals["provider_attempts"],
        "committed_actions": outcomes["committed"],
        "compactions": compactions,
        "continuations": continuations,
        "report_attempts": report_attempts,
    }
    recorded = snapshot.counters.model_dump()
    checks = [
        CounterCheck(
            metric=metric,
            recorded=recorded[metric],
            derived=value,
            agrees=recorded[metric] == value,
        )
        for metric, value in derived.items()
    ]

    return RunAnalytics(
        run_id=snapshot.run_id,
        order_id=snapshot.order_id,
        observed_at=observed_at,
        through_sequence=cutoff,
        recorded_revision=snapshot.recorded_revision,
        status=snapshot.status,
        close_reason=snapshot.close_reason,
        started_at=snapshot.started_at,
        closed_at=closed_at,
        duration_seconds=max(int(duration.total_seconds()), 0),
        unique_events=unique_events,
        duplicate_events=duplicate_events,
        deferred_events=deferred_events,
        events_by_type=events_by_type,
        decision_episodes=episodes,
        completed_episodes=completed,
        discarded_episodes=discarded,
        episodes_by_trigger=TriggerBreakdown(**triggers),
        provider_attempts=totals["provider_attempts"],
        report_attempts=report_attempts,
        action_outcomes=ActionOutcomes(**outcomes),
        committed_by_action=committed_by_action,
        blocked_by_reason=blocked_by_reason,
        review_flags=review_flags,
        review_outcomes=review_outcomes,
        open_issues=len(snapshot.facts.open_issues),
        escalated_issues=sum(
            1 for issue in snapshot.facts.open_issues if issue.review_required
        ),
        compactions=compactions,
        refused_compactions=refused_compactions,
        continuations=continuations,
        prepared_continuations=prepared_continuations,
        operational_failures=failures,
        failures_by_kind=failures_by_kind,
        tokens=TokenUsage(
            # A provider that reported nothing leaves these absent rather than zero:
            # "nobody told us" and "it used none" are different statements.
            input_tokens=totals["input_tokens"] if reported_calls else None,
            output_tokens=totals["output_tokens"] if reported_calls else None,
            calls=totals["provider_calls"],
            reported_calls=reported_calls,
        ),
        counter_checks=checks,
    )
