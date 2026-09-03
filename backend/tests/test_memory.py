"""T11 — what compaction is allowed to forget.

The failure this guards against is a fluent summary that quietly drops a standing
instruction or an unanswered question, and a version number that goes up because
something happened rather than because the narrative moved.
"""

from datetime import timedelta
from uuid import uuid4

from app.contracts.decision import MemoryRefresh
from app.domain import memory
from app.domain.presets import PRESETS, demo_timing
from app.domain.vocabulary import COMPACTION_RECORDS, DEMO_COMPACTION_RECORDS, SUMMARY_CHARS
from tests.conftest import RULES_NOW, sample_snapshot


def summarised(through: int, *, version: int = 1, text: str = "An earlier summary.", **overrides):
    return sample_snapshot(
        memory={
            "text": text,
            "summary_version": version,
            "summary_through_sequence": through,
            "recorded_at": RULES_NOW.isoformat(),
        },
        **overrides,
    )


def refund_and_instruction(**overrides):
    """An old unanswered question and an old restriction, both still binding."""
    identity = uuid4()
    return summarised(
        10,
        facts={
            "shipment": "in_transit",
            "shipment_reference": "SHP-1",
            "open_issues": [
                {
                    "issue_id": "refund",
                    "description": "Refund requested: arrived damaged",
                    "evidence": [{"sequence": 10, "activity_id": str(uuid4())}],
                }
            ],
        },
        instructions=[
            {
                "instruction_id": identity,
                "text": "Do not contact the customer without human review.",
                "added_at": (RULES_NOW - timedelta(hours=2)).isoformat(),
                "source_command_id": identity,
                "policy_changes": {"require_customer_review": True},
            }
        ],
        **overrides,
    )


# --- when a refresh is due ----------------------------------------------------------------


def test_a_summary_is_not_rewritten_for_every_recorded_change():
    assert memory.refresh_due(summarised(10, **{"last_sequence": 12})) is False
    behind = summarised(10, **{"last_sequence": 10 + COMPACTION_RECORDS})
    assert memory.refresh_due(behind) is True


def test_a_run_with_no_summary_yet_always_needs_one():
    assert memory.refresh_due(sample_snapshot()) is True


def test_a_demo_template_compacts_sooner_so_it_can_be_watched():
    demo = demo_timing(PRESETS[0], "short_review")
    snapshot = summarised(10, supervisor=demo, last_sequence=10 + DEMO_COMPACTION_RECORDS)
    assert memory.compaction_threshold(snapshot) == DEMO_COMPACTION_RECORDS
    assert memory.refresh_due(snapshot) is True


# --- what survives a compaction ------------------------------------------------------------


def test_an_old_unanswered_question_survives_a_newer_narrative():
    """The refund arrived at sequence 10 and nothing has resolved it since."""
    snapshot = refund_and_instruction(last_sequence=60)
    compaction = memory.deterministic(snapshot, now=RULES_NOW, reason="due")

    assert "refund" in compaction.summary.text
    assert compaction.summary.summary_through_sequence == 60
    assert compaction.covered_from == 10
    # Compaction rewrites the narrative and nothing else.
    assert snapshot.facts.open_issues[0].issue_id == "refund"
    assert len(snapshot.instructions) == 1


def test_a_standing_instruction_is_never_summarised_away():
    snapshot = refund_and_instruction(last_sequence=60)
    compaction = memory.deterministic(snapshot, now=RULES_NOW, reason="due")
    assert "1 standing instruction" in compaction.summary.text
    assert snapshot.instructions[0].text.startswith("Do not contact")


def test_a_crowded_narrative_trims_whole_sentences_and_stays_within_its_cap():
    crowded = summarised(
        1,
        facts={
            "open_issues": [
                {
                    "issue_id": f"concern-{index}",
                    "description": "A long description repeated to crowd the summary. " * 4,
                    "evidence": [{"sequence": index + 2, "activity_id": str(uuid4())}],
                }
                for index in range(12)
            ]
        },
    )
    text = memory.render_summary(crowded)
    assert len(text) <= SUMMARY_CHARS
    assert text.endswith(".")


# --- a model-written summary ---------------------------------------------------------------


def test_a_proposed_summary_is_adopted_over_the_range_it_declares():
    snapshot = summarised(10, last_sequence=60)
    outcome = memory.from_proposal(
        snapshot,
        MemoryRefresh(
            text="Payment cleared; the refund question is still open.", through_sequence=55
        ),
        input_cutoff=55,
        decision_reference="run/decision/4",
        now=RULES_NOW,
    )
    assert isinstance(outcome, memory.Compaction)
    assert outcome.summary.provenance == "model"
    assert outcome.summary.summary_through_sequence == 55
    assert outcome.summary.source_decision_id == "run/decision/4"
    assert outcome.summary.summary_version == 2


def test_a_summary_cannot_claim_evidence_the_decision_never_received():
    snapshot = summarised(10, last_sequence=60)
    outcome = memory.from_proposal(
        snapshot,
        MemoryRefresh(text="Everything is fine.", through_sequence=60),
        input_cutoff=42,
        decision_reference="run/decision/4",
        now=RULES_NOW,
    )
    assert isinstance(outcome, memory.RefusedRefresh)
    assert outcome.reason == "cutoff_beyond_input"


def test_a_summary_that_covers_less_than_the_current_one_is_refused():
    snapshot = summarised(40, last_sequence=60)
    outcome = memory.from_proposal(
        snapshot,
        MemoryRefresh(text="A shorter view.", through_sequence=20),
        input_cutoff=60,
        decision_reference="run/decision/4",
        now=RULES_NOW,
    )
    assert isinstance(outcome, memory.RefusedRefresh)
    assert outcome.reason == "cutoff_regressed"


def test_a_refused_summary_leaves_the_previous_one_untouched():
    snapshot = summarised(40, text="The summary that still stands.", last_sequence=60)
    memory.from_proposal(
        snapshot,
        MemoryRefresh(text="Replacement.", through_sequence=20),
        input_cutoff=60,
        decision_reference="run/decision/4",
        now=RULES_NOW,
    )
    assert snapshot.memory.text == "The summary that still stands."
    assert snapshot.memory.summary_version == 1
