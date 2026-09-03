"""T11's other half — evidence that was recorded but never actually reasoned over.

Storing an input is not the same as considering it. The trap these cover is a run that
looks healthy because everything was written down, while the oldest unanswered question
quietly falls out of the recent window and is never seen again.
"""

from uuid import uuid4

from app.contracts.decision import EvidenceDetail
from app.domain import assembly
from app.domain.vocabulary import CONTEXT_BUDGET_BYTES, DEFERRED_EVIDENCE_LIMIT
from tests.conftest import RULES_NOW, sample_snapshot


def references(*sequences: int) -> list[dict]:
    return [{"sequence": sequence, "activity_id": str(uuid4())} for sequence in sequences]


def detail(sequence: int, explanation: str = "Something happened.") -> EvidenceDetail:
    return EvidenceDetail(
        sequence=sequence,
        kind="event",
        disposition="applied",
        recorded_at=RULES_NOW,
        explanation=explanation,
    )


def run(*, considered_through: int = 0, **overrides):
    return sample_snapshot(last_decision_through_sequence=considered_through, **overrides)


# --- what a decision asks to read ---------------------------------------------------------


def test_evidence_past_the_last_decision_is_still_awaiting_consideration():
    snapshot = run(considered_through=10, deferred_evidence=references(12, 14))
    plan = assembly.plan(snapshot)
    assert plan.unconsidered == (12, 14)


def test_evidence_a_decision_already_covered_is_not_raised_again():
    snapshot = run(considered_through=20, deferred_evidence=references(12, 14))
    assert assembly.plan(snapshot).unconsidered == ()


def test_an_open_concern_keeps_its_evidence_in_view():
    """A refund question from an hour ago outranks the third routine update."""
    snapshot = run(considered_through=5, unresolved_evidence=references(10))
    assert 10 in assembly.plan(snapshot).unconsidered


def test_the_recent_window_cannot_crowd_out_an_unanswered_question():
    snapshot = run(
        considered_through=5,
        unresolved_evidence=references(10),
        recent_evidence=references(*range(40, 52)),
    )
    plan = assembly.plan(snapshot)
    assert plan.unconsidered == (10,)
    assert 10 not in plan.considered, "it is listed once, as unconsidered"
    assert len(plan.considered) == 12


def test_the_number_of_unconsidered_references_is_bounded():
    snapshot = run(considered_through=0, deferred_evidence=references(*range(1, 40)))
    assert len(assembly.plan(snapshot).unconsidered) == DEFERRED_EVIDENCE_LIMIT


def test_a_reference_appearing_twice_is_read_once():
    snapshot = run(
        considered_through=0,
        deferred_evidence=references(7),
        unresolved_evidence=references(7),
        recent_evidence=references(7),
    )
    assert assembly.plan(snapshot).sequences == (7,)


def test_records_come_back_sorted_into_considered_and_not():
    plan = assembly.EvidencePlan(considered=(3,), unconsidered=(9,))
    split = assembly.split(plan, [detail(3), detail(9)])
    assert [record.sequence for record in split.considered] == [3]
    assert [record.sequence for record in split.unconsidered] == [9]


# --- coverage only advances when a decision genuinely covered it ---------------------------


def test_an_event_arriving_during_inference_stays_pending():
    """The decision's input cutoff was 10; entry 15 landed while it was thinking."""
    snapshot = run(deferred_evidence=references(4, 15))
    remaining = assembly.still_pending(snapshot, cutoff=10)
    assert [item["sequence"] for item in remaining] == [15]


def test_a_decision_covering_everything_leaves_nothing_pending():
    snapshot = run(deferred_evidence=references(4, 9))
    assert assembly.still_pending(snapshot, cutoff=10) == []


# --- the budget -----------------------------------------------------------------------------


def test_an_ordinary_context_fits_and_an_oversized_one_is_reported():
    assert assembly.over_budget({"small": "payload"}) is None
    oversized = {"filler": "x" * (CONTEXT_BUDGET_BYTES + 10)}
    reported = assembly.over_budget(oversized)
    assert reported is not None and reported > CONTEXT_BUDGET_BYTES
