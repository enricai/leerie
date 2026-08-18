"""Tests for the source-of-truth validation gate in gather_answers().

Covers the validation that rejects invalid pre-supplied answers, and
the non-interactive flow where the resolved preference satisfies
source_of_truth without asking the user.
"""
from __future__ import annotations

import io
import json
import sys

import pytest


@pytest.fixture(params=[True, False], ids=["needs_sot", "no_needs_sot"])
def state(request, leerie, tmp_path):
    """A fresh State at a tmp_path/.leerie/runs/<run-id>/, with pref='both'.

    **Parametrized over `needs_source_of_truth` on purpose.** This fixture
    hardcoded `True`, so every test in this file — including the
    three-value `test_preference_satisfies_source_of_truth` sweep —
    exercised only the branch where the classifier flagged the question.
    The file therefore reported full coverage of a contract the code
    honoured on one branch: with the flag `False`, `gather_answers` wrote
    nothing and every consumer fell back to a hardcoded `"codebase"`.
    Measured across the run corpus, 74 of 196 runs took that branch.

    Parametrizing here is what makes the whole file falsify the defect
    rather than pass beside it. Do not collapse it back to a single
    value.
    """
    leerie_root = tmp_path / ".leerie"
    run_id = "test-run-aaa111"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {
        "task": "test task",
        "categories": ["feature-implementation"],
        "classifier_questions": [],
        "needs_source_of_truth": request.param,
        "source_of_truth_pref": "both",
    }
    return st


@pytest.fixture
def non_tty_stdin(monkeypatch):
    """Force sys.stdin to a non-TTY so gather_answers takes the defer path."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))


# --- validation gate -------------------------------------------------------

@pytest.mark.parametrize("bad_value", [
    "codbase",                 # typo
    "existing-patterns",       # not in the enum
    "researched-standards",    # not in the enum
])
def test_invalid_value_rejected(leerie, state, capsys, bad_value):
    with pytest.raises(SystemExit) as exc:
        leerie.gather_answers(state, {"source_of_truth": bad_value})
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "is not one of" in err
    # The enum itself, not just the phrase. Dropping the values from the
    # message — "is not one of the accepted values", with no list — leaves
    # an operator no way to know what IS accepted, and passed before this.
    for v in leerie.SOURCE_OF_TRUTH_VALUES:
        assert v in err, f"the error omits the accepted value {v!r}"
    assert bad_value in err


@pytest.mark.parametrize("value", ["codebase", "research", "both"])
def test_valid_values_pass(leerie, state, value):
    answers = leerie.gather_answers(state, {"source_of_truth": value})
    assert answers["source_of_truth"] == value


# --- preference fills in source_of_truth without asking --------------------

@pytest.mark.parametrize("pref", ["codebase", "research", "both"])
def test_preference_satisfies_source_of_truth(leerie, state, pref):
    """gather_answers fills source_of_truth from the resolved preference,
    without prompting the user or deferring to pending-questions.json."""
    state.data["source_of_truth_pref"] = pref
    answers = leerie.gather_answers(state, None)
    assert answers["source_of_truth"] == pref
    assert not (state.path.parent / "pending-questions.json").exists()


def test_default_preference_is_both(leerie, state):
    """With the new default, source_of_truth is filled with 'both' when
    nothing else is specified."""
    answers = leerie.gather_answers(state, None)
    assert answers["source_of_truth"] == "both"


# --- non-TTY defer path: only fires for classifier intent questions --------

def test_defer_writes_pending_for_classifier_questions(
        leerie, state, non_tty_stdin):
    """When the classifier surfaced intent questions and stdin is non-TTY,
    gather_answers writes pending-questions.json and exits 10. The file
    contains only the questions; source-of-truth was already satisfied
    from the preference."""
    state.data["classifier_questions"] = [
        {"id": "q1", "question": "Is the bug intermittent?",
         "why_underivable": "user-specific"}
    ]
    with pytest.raises(SystemExit) as exc:
        leerie.gather_answers(state, None)
    assert exc.value.code == leerie.EXIT_NEEDS_ANSWERS

    pq = json.loads((state.path.parent / "pending-questions.json").read_text())
    assert pq == {"questions": [
        {"id": "q1", "question": "Is the bug intermittent?",
         "why_underivable": "user-specific"}
    ]}


def test_no_defer_when_no_classifier_questions(leerie, state, non_tty_stdin):
    """No classifier questions + source-of-truth satisfied from preference
    → no defer file, no exit."""
    answers = leerie.gather_answers(state, None)
    assert answers["source_of_truth"] == "both"
    assert not (state.path.parent / "pending-questions.json").exists()


# --- clarify default (asking is opt-in via --clarify) ---------------------

def test_default_mode_satisfies_sot_from_preference(leerie, state):
    """In the default mode (no --clarify), source_of_truth still comes
    from the resolved preference. Asking is opt-in, but the preference
    still fills the answer non-interactively."""
    state.data["source_of_truth_pref"] = "research"
    state.data["clarify"] = False
    answers = leerie.gather_answers(state, None)
    assert answers["source_of_truth"] == "research"
