"""T01 and T10 for generated guidance — what a hint is not allowed to do.

The whole reason the classifier stays a small deterministic thing is that its behaviour
can be read before it runs. Guidance is a few typed parameters it consults *after* the
non-negotiable rules, so these tests are mostly about the order: a hint that arrives
hoping to talk its way past a terminal event, an operator restriction, or an unfamiliar
payload never gets the chance.
"""

from datetime import timedelta
from uuid import uuid4

import pytest

from app.contracts.commands import PolicyChanges
from app.contracts.run import ContextStamp, WakeGuidance, WakeHint
from app.domain import events as event_rules
from app.domain import guidance, lifecycle, policy
from app.domain.presets import PRESETS
from app.domain.vocabulary import GUIDANCE_HINTS
from tests.conftest import RULES_NOW, sample_event, sample_evidence, sample_snapshot

LATER = RULES_NOW + timedelta(hours=4)


def hint(kind: str = "watch_for_progress", **fields) -> WakeHint:
    defaults = {
        "watch_for_progress": {
            "issue_id": event_rules.STALLED_ISSUE,
            "event_type": "shipment_created",
        },
        "shorten_review": {
            "issue_id": event_rules.STALLED_ISSUE,
            "review_after_seconds": 60,
        },
        "defer_routine": {"event_type": "payment_confirmed"},
    }[kind]
    return WakeHint.model_validate({"kind": kind, "expires_at": LATER} | defaults | fields)


def stamp(snapshot) -> ContextStamp:
    return ContextStamp(
        context_version=snapshot.context_version,
        control_epoch=snapshot.control_epoch,
        evidence_through_sequence=snapshot.last_sequence,
    )


def proposed(*hints, context=None) -> WakeGuidance:
    return WakeGuidance(version=1, context=context, hints=list(hints))


def stalled(**overrides):
    """A run with an open overdue-progress concern a hint can legitimately reference."""
    facts = overrides.pop(
        "facts",
        {
            "open_issues": [
                {
                    "issue_id": event_rules.STALLED_ISSUE,
                    "description": "No order progress recorded for 6 hours.",
                    "evidence": [{"sequence": 3, "activity_id": str(uuid4())}],
                }
            ]
        },
    )
    return sample_snapshot(facts=facts, **overrides)


def concerned(**overrides):
    """An open question that is not about progress, so progress stays routine."""
    return sample_snapshot(
        facts={
            "open_issues": [
                {
                    "issue_id": event_rules.REFUND_ISSUE,
                    "description": "Refund requested: arrived damaged",
                    "evidence": [{"sequence": 4, "activity_id": str(uuid4())}],
                }
            ]
        },
        **overrides,
    )


def carrying(snapshot, *hints, **overrides):
    """The same run, with guidance already adopted."""
    document = snapshot.model_dump(mode="json")
    document["wake_guidance"] = proposed(
        *hints, context=stamp(snapshot)
    ).model_dump(mode="json")
    document.update(overrides)
    return type(snapshot).model_validate(document)


def interpret(snapshot, command):
    return event_rules.interpret(snapshot, command, now=RULES_NOW, evidence=sample_evidence())


# --- the hint vocabulary is finite ----------------------------------------------------------


@pytest.mark.parametrize(
    "invalid",
    [
        {"kind": "watch_for_progress", "expires_at": LATER, "issue_id": "x"},
        {"kind": "shorten_review", "expires_at": LATER, "issue_id": "x"},
        {"kind": "defer_routine", "expires_at": LATER, "event_type": "payment_confirmed",
         "issue_id": "x"},
        {"kind": "generated_rule", "expires_at": LATER},
    ],
)
def test_a_hint_outside_the_supported_shapes_is_not_a_hint(invalid):
    with pytest.raises(ValueError):
        WakeHint.model_validate(invalid)


# --- validation -----------------------------------------------------------------------------


def test_the_schema_does_not_constrain_an_issue_id_the_gate_already_enforces():
    """A strict validator throws away the whole answer over one bad enum value, and an
    unknown concern is a hint to refuse rather than a decision to lose."""
    from app.agent.schema import proposal_schema

    schema = proposal_schema(
        ["create_internal_note"],
        minimum=30,
        maximum=3600,
        cutoff=10,
        offer_memory=False,
        issue_ids=["refund"],
    )
    hint_fields = schema["properties"]["wake_hints"]["anyOf"][0]["items"]["properties"]
    assert "enum" not in hint_fields["issue_id"]["anyOf"][0]
    assert "refund" in hint_fields["issue_id"]["anyOf"][0]["description"]


def test_a_usable_hint_is_adopted():
    snapshot = stalled()
    review = guidance.check(proposed(hint(), context=stamp(snapshot)), snapshot, stamp(snapshot),
                            now=RULES_NOW)
    assert review.usable and review.refused == ()


@pytest.mark.parametrize(
    "broken, reason",
    [
        ({"expires_at": RULES_NOW - timedelta(minutes=1)}, "expired"),
        ({"issue_id": "invented-concern"}, "unknown_issue"),
    ],
)
def test_a_hint_that_cannot_stand_up_is_refused_individually(broken, reason):
    snapshot = stalled()
    review = guidance.check(
        proposed(hint(**broken), context=stamp(snapshot)), snapshot, stamp(snapshot), now=RULES_NOW
    )
    assert not review.usable
    assert review.refused[0].reason == reason


def test_a_hint_cannot_outlive_the_order_it_is_about():
    snapshot = stalled()
    beyond = hint(expires_at=snapshot.maximum_age_at + timedelta(hours=1))
    review = guidance.check(
        proposed(beyond, context=stamp(snapshot)), snapshot, stamp(snapshot), now=RULES_NOW
    )
    assert review.refused[0].reason == "outlives_the_order"


def test_a_review_interval_outside_the_template_range_is_refused():
    """The absolute bound is the DTO's; the template's own range is checked here."""
    snapshot = stalled()
    profile = snapshot.supervisor.wake_profile
    with pytest.raises(ValueError):
        hint("shorten_review", review_after_seconds=99_999)

    under = hint("shorten_review", review_after_seconds=profile.minimum_seconds - 1)
    review = guidance.check(
        proposed(under, context=stamp(snapshot)), snapshot, stamp(snapshot), now=RULES_NOW
    )
    assert review.refused[0].reason == "interval_out_of_range"


def test_guidance_written_for_a_context_that_moved_is_refused_whole():
    snapshot = stalled(context_version=6)
    written_for = stamp(snapshot).model_copy(update={"context_version": 5})
    review = guidance.check(
        proposed(hint(), context=written_for), snapshot, stamp(snapshot), now=RULES_NOW
    )
    assert not review.usable
    assert review.refused[0].reason == "stale_context"


@pytest.mark.parametrize("event_type", ["delivered", "shipment_delayed", "refund_requested"])
def test_nothing_that_matters_can_be_arranged_into_routine(event_type):
    snapshot = stalled()
    review = guidance.check(
        proposed(hint("defer_routine", event_type=event_type), context=stamp(snapshot)),
        snapshot,
        stamp(snapshot),
        now=RULES_NOW,
    )
    assert review.refused[0].reason == "not_routine"


def test_only_the_first_few_hints_are_considered():
    snapshot = stalled()
    with pytest.raises(ValueError):
        proposed(*[hint() for _ in range(GUIDANCE_HINTS + 1)], context=stamp(snapshot))


# --- what an adopted hint actually changes ---------------------------------------------------


def test_a_watched_event_wakes_the_agent_that_would_otherwise_have_deferred():
    """The demonstrable case, and the difference it makes.

    A shipment being created while a refund question is open is ordinary progress: the
    template records it and waits for the next scheduled review. The agent, which knows
    it is waiting to answer the customer, asks to be woken by exactly that event.
    """
    snapshot = concerned()
    arriving = sample_event("shipment_created", {"shipment_reference": "SHP-1"})

    without = policy.classify(interpret(snapshot, arriving), snapshot, "shipment_created")
    assert without.outcome == "deferred", "ordinary progress does not wake the agent"

    watched = carrying(snapshot, hint(issue_id=event_rules.REFUND_ISSUE))
    with_hint = policy.classify(
        interpret(watched, arriving),
        watched,
        "shipment_created",
        hints=guidance.active(watched, now=RULES_NOW),
        guidance_version=1,
    )
    assert with_hint.wake is True
    assert with_hint.guidance_hint == "watch_for_progress"
    assert with_hint.guidance_version == 1
    assert "asked to be woken" in with_hint.reason


def test_a_terminal_event_is_decided_before_guidance_is_even_consulted():
    watched = carrying(stalled(), hint("defer_routine", event_type="payment_confirmed"))
    delivery = sample_event("delivered", {"evidence_reference": "POD-1"})
    outcome = interpret(watched, delivery)
    verdict = policy.classify(
        outcome, watched, "delivered", hints=guidance.active(watched, now=RULES_NOW)
    )
    assert outcome.terminal is True
    assert verdict.guidance_hint is None, "guidance had no say"


def test_an_unfamiliar_event_still_escalates_with_guidance_in_place():
    watched = carrying(stalled(), hint())
    outcome = interpret(watched, sample_event("warehouse_exception", {"bay": "B1"}))
    verdict = policy.classify(
        outcome, watched, "warehouse_exception", hints=guidance.active(watched, now=RULES_NOW)
    )
    assert verdict.review_required is True and verdict.wake is True
    assert verdict.guidance_hint is None


def test_an_operator_escalation_outranks_a_hint():
    identity = uuid4()
    escalating = stalled(
        instructions=[
            {
                "instruction_id": identity,
                "text": "If shipment is delayed, escalate immediately.",
                "added_at": RULES_NOW,
                "source_command_id": identity,
                "policy_changes": PolicyChanges(escalate_shipment_delays=True).model_dump(),
            }
        ]
    )
    watched = carrying(escalating, hint("defer_routine", event_type="payment_confirmed"))
    outcome = interpret(watched, sample_event("shipment_delayed", {"reason": "Backlog"}))
    verdict = policy.classify(
        outcome, watched, "shipment_delayed", hints=guidance.active(watched, now=RULES_NOW)
    )
    assert verdict.wake is True
    assert "standing instruction" in verdict.reason


def test_routine_progress_is_deferred_only_when_nothing_is_unresolved():
    settled = sample_snapshot()
    watched = carrying(settled, hint("defer_routine", event_type="payment_confirmed"))
    confirmed = sample_event("payment_confirmed", {"payment_reference": "PAY-1"})
    verdict = policy.classify(
        interpret(watched, confirmed),
        watched,
        "payment_confirmed",
        hints=guidance.active(watched, now=RULES_NOW),
    )
    assert verdict.outcome == "deferred"
    assert verdict.guidance_hint == "defer_routine"


# --- guidance is perishable -------------------------------------------------------------------


def test_a_hint_stops_applying_once_its_concern_is_settled():
    watched = carrying(stalled(), hint())
    assert guidance.active(watched, now=RULES_NOW), "the concern is open"
    resolved = carrying(sample_snapshot(), hint())
    assert guidance.active(resolved, now=RULES_NOW) == ()


def test_a_hint_stops_applying_once_it_expires():
    watched = carrying(stalled(), hint())
    assert guidance.active(watched, now=LATER + timedelta(seconds=1)) == ()


def test_an_operator_boundary_retires_guidance_written_before_it():
    watched = carrying(stalled(), hint(), control_epoch=3)
    assert guidance.active(watched, now=RULES_NOW) == ()


def test_a_shortened_review_brings_the_next_look_forward_but_never_pushes_it_out():
    snapshot = stalled()
    profile = snapshot.supervisor.wake_profile
    watched = carrying(snapshot, hint("shorten_review", review_after_seconds=60))

    schedule = lifecycle.effective_wake(None, watched, now=RULES_NOW)
    assert schedule.deadline - RULES_NOW <= timedelta(seconds=60)

    # A longer proposal is now outside the permitted horizon.
    from app.contracts.decision import DecisionProposal

    longer = lifecycle.effective_wake(
        DecisionProposal(rationale="Later", sleep_for_seconds=profile.default_seconds),
        watched,
        now=RULES_NOW,
    )
    assert longer.used_default is True
    assert longer.deadline - RULES_NOW <= timedelta(seconds=60)


def test_a_template_without_guidance_behaves_exactly_as_before():
    snapshot = sample_snapshot(supervisor=PRESETS[0])
    assert guidance.active(snapshot, now=RULES_NOW) == ()
    assert lifecycle.effective_wake(None, snapshot, now=RULES_NOW).used_default is True
