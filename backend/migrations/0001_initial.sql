-- Three logical entities: supervisor configurations, order runs, activity records.
-- Typed columns carry what the API filters and orders by. The bounded RunSnapshot
-- lives in `runs.snapshot`; the CHECK constraints below keep the mirrored columns
-- and that document from drifting apart.

CREATE TABLE supervisors (
    id uuid PRIMARY KEY,
    name text NOT NULL,
    version integer NOT NULL CHECK (version >= 1),
    is_preset boolean NOT NULL DEFAULT false,
    config jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    CHECK (name = config ->> 'name'),
    CHECK (version = (config ->> 'version')::integer),
    CHECK (id::text = config ->> 'id')
);

CREATE TABLE runs (
    id uuid PRIMARY KEY,
    order_id text NOT NULL UNIQUE,
    workflow_id text NOT NULL UNIQUE,
    supervisor_id uuid NOT NULL REFERENCES supervisors (id),
    create_command_id uuid NOT NULL UNIQUE,
    create_request_digest text NOT NULL CHECK (create_request_digest ~ '^[a-f0-9]{64}$'),
    initial_event_id uuid NOT NULL,
    template_snapshot jsonb NOT NULL,
    initial_context jsonb NOT NULL,
    status text NOT NULL CHECK (
        status IN (
            'starting', 'evaluating', 'applying', 'sleeping', 'paused',
            'awaiting_recovery', 'finalizing', 'completed', 'terminated', 'expired'
        )
    ),
    pending_control text CHECK (pending_control IN ('pause', 'interrupt', 'resume', 'terminate')),
    close_reason text CHECK (
        close_reason IN ('delivered', 'manually_terminated', 'maximum_age_reached')
    ),
    recorded_revision bigint NOT NULL CHECK (recorded_revision >= 0),
    context_version bigint NOT NULL CHECK (context_version >= 0),
    control_epoch bigint NOT NULL CHECK (control_epoch >= 0),
    last_sequence bigint NOT NULL CHECK (last_sequence >= 0),
    started_at timestamptz NOT NULL,
    maximum_age_at timestamptz NOT NULL,
    next_wake_at timestamptz,
    updated_at timestamptz NOT NULL,
    closed_at timestamptz,
    execution_generation integer NOT NULL DEFAULT 0 CHECK (execution_generation >= 0),
    snapshot jsonb NOT NULL,
    final_output jsonb,
    CHECK (maximum_age_at > started_at),
    -- One serialization contract: the mirrored columns must equal the stored snapshot.
    CHECK (order_id = snapshot ->> 'order_id'),
    CHECK (workflow_id = snapshot ->> 'workflow_id'),
    CHECK (workflow_id = 'order-supervisor/' || id::text),
    CHECK (status = snapshot ->> 'status'),
    CHECK (recorded_revision = (snapshot ->> 'recorded_revision')::bigint),
    CHECK (last_sequence = (snapshot ->> 'last_sequence')::bigint),
    -- A closed run must carry its saved report; an open run must not claim one.
    CHECK ((close_reason IS NULL) = (closed_at IS NULL)),
    CHECK ((closed_at IS NULL) = (final_output IS NULL))
);

-- The run list filters by state and pages on (updated_at, id).
CREATE INDEX runs_status_listing ON runs (status, updated_at DESC, id DESC);
CREATE INDEX runs_listing ON runs (updated_at DESC, id DESC);

CREATE TABLE activity_log (
    id uuid PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES runs (id),
    sequence bigint NOT NULL CHECK (sequence >= 1),
    kind text NOT NULL CHECK (
        kind IN (
            'run_reserved', 'event', 'policy', 'decision', 'action', 'instruction',
            'control', 'review', 'sleep', 'memory', 'continuation', 'recovery',
            'finalization', 'operation_receipt'
        )
    ),
    occurred_at timestamptz,
    recorded_at timestamptz NOT NULL,
    command_id uuid,
    event_id text,
    operation_id text,
    decision_id text,
    action_id text,
    disposition text NOT NULL CHECK (
        disposition IN (
            'applied', 'duplicate', 'conflict', 'rejected', 'too_late', 'capacity_exceeded',
            'deferred', 'wake_now', 'review_required', 'proposed', 'blocked',
            'pending_review', 'committed', 'failed', 'recorded'
        )
    ),
    explanation text NOT NULL,
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Prefixed identity (`command:`, `event:`, `action:`, `operation:`) claimed by the
    -- one canonical record. Its digest separates redelivery from conflicting reuse.
    dedupe_key text,
    dedupe_digest text CHECK (dedupe_digest ~ '^[a-f0-9]{64}$'),
    CHECK (dedupe_digest IS NULL OR dedupe_key IS NOT NULL),
    UNIQUE (run_id, sequence)
);

CREATE UNIQUE INDEX activity_log_dedupe
    ON activity_log (run_id, dedupe_key)
    WHERE dedupe_key IS NOT NULL;
