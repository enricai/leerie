"""Unit tests for phase_classify's OR-preserve write of
likely_already_satisfied / likely_already_satisfied_evidence.

Distinct from tests/test_phase_classification_gate.py's coverage (which
stubs phase_classify entirely and exercises only the gate's exhaustion
routing): these tests exercise phase_classify's own write site directly,
with claude_p stubbed as its only external dependency, to pin the actual
code change — a fresh True+evidence claim always wins; a falsy/absent
claim must not clear an already-persisted True+evidence claim.
"""
from __future__ import annotations

import asyncio


def _minimal_state(leerie, tmp_path, run_id="test-classify-or-preserve"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"classifier": "sonnet"}
EFFORTS = {"classifier": "medium"}


def test_fresh_true_with_evidence_is_persisted(leerie, tmp_path, monkeypatch):
    """Baseline: a classifier round that sets the field with evidence
    persists it, same as before this fix."""
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories": ["bug-fixing"],
                "likely_already_satisfied": True,
                "likely_already_satisfied_evidence": "commit abc123 already ships this"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert st.data["likely_already_satisfied"] is True
    assert st.data["likely_already_satisfied_evidence"] == "commit abc123 already ships this"


def test_false_on_first_round_persists_false(leerie, tmp_path, monkeypatch):
    """Baseline: nothing to preserve yet, so a False round writes False —
    identical to pre-fix behavior."""
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories": ["bug-fixing"]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert st.data["likely_already_satisfied"] is False
    assert st.data["likely_already_satisfied_evidence"] == ""


def test_later_false_round_does_not_clear_earlier_true(leerie, tmp_path, monkeypatch):
    """The actual fix: reproduces the real incident at the phase_classify
    layer directly. Round 1 sets True+evidence; round 2 (a fresh
    phase_classify call, as phase_classification_gate's _on_feedback
    would drive) comes back False/absent. The earlier True+evidence must
    survive."""
    st = _minimal_state(leerie, tmp_path)
    first_evidence = "ConfirmDialog + all 7 fixes already on HEAD at cb4f8be"

    calls = {"n": 0}

    async def fake_claude_p(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"categories": ["bug-fixing", "documentation"],
                     "likely_already_satisfied": True,
                     "likely_already_satisfied_evidence": first_evidence}
        # Round 2: a fresh classifier call that reverts to a bare category
        # guess, omitting the satisfied claim entirely.
        return {"categories": ["bug-fixing"]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))

    assert st.data["likely_already_satisfied"] is True
    assert st.data["likely_already_satisfied_evidence"] == first_evidence


def test_fresh_true_with_evidence_overrides_prior_true(leerie, tmp_path, monkeypatch):
    """A genuinely fresh True+evidence claim always wins and replaces the
    prior evidence text — this is still a real, independently-derived
    claim, not a stale one being blindly kept."""
    st = _minimal_state(leerie, tmp_path)
    first_evidence = "first finding"
    second_evidence = "second, more specific finding"

    calls = {"n": 0}

    async def fake_claude_p(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"categories": ["bug-fixing"],
                     "likely_already_satisfied": True,
                     "likely_already_satisfied_evidence": first_evidence}
        return {"categories": ["bug-fixing"],
                 "likely_already_satisfied": True,
                 "likely_already_satisfied_evidence": second_evidence}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))

    assert st.data["likely_already_satisfied"] is True
    assert st.data["likely_already_satisfied_evidence"] == second_evidence


def test_true_without_evidence_does_not_count_as_a_prior_true(leerie, tmp_path, monkeypatch):
    """A True claim with empty evidence is not a valid 'prior True' worth
    preserving — mirrors the EMPTY_EVIDENCE discipline elsewhere. If round
    1 sets True with no evidence (which check_classifier_output would
    normally flag as an issue and retry within phase_classify's own inner
    loop, but simulate the edge case directly here) and round 2 sets
    False, the final state must be False — there was nothing valid to
    preserve."""
    st = _minimal_state(leerie, tmp_path)

    calls = {"n": 0}

    async def fake_claude_p(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"categories": ["bug-fixing"],
                     "likely_already_satisfied": True,
                     "likely_already_satisfied_evidence": ""}
        return {"categories": ["bug-fixing"]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))

    assert st.data["likely_already_satisfied"] is False
    assert st.data["likely_already_satisfied_evidence"] == ""


def test_true_without_evidence_on_a_single_round_never_persists_as_bare_true(
        leerie, tmp_path, monkeypatch):
    """Regression pin for a real bug caught on a second review pass: a
    single phase_classify call (no prior round at all — fresh state) that
    returns likely_already_satisfied=True with empty evidence must NOT
    persist likely_already_satisfied=True with empty evidence. That
    combination violates the invariant every consumer of this field
    relies on (phase_classification_gate's exhaustion check, the
    EMPTY_EVIDENCE self-check) — a True is only ever meaningful paired
    with real evidence. An earlier version of the OR-preserve fix wrote
    `st.data["likely_already_satisfied"] = new_satisfied` in the "no
    valid prior" branch, which persisted a bare True here; the fix writes
    False in that branch unconditionally, since reaching it means there
    is no valid (True + evidence) claim from any source."""
    st = _minimal_state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"categories": ["bug-fixing"],
                "likely_already_satisfied": True,
                "likely_already_satisfied_evidence": ""}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))

    assert st.data["likely_already_satisfied"] is False
    assert st.data["likely_already_satisfied_evidence"] == ""


def test_invalid_claim_does_not_clobber_a_valid_prior_true(leerie, tmp_path, monkeypatch):
    """A round returning True with empty evidence, on top of an already
    valid (True + real evidence) prior claim, must not clobber the valid
    prior — the invalid round simply contributes nothing, neither
    overriding nor clearing."""
    st = _minimal_state(leerie, tmp_path)
    real_evidence = "the fix is already on HEAD at commit xyz"

    calls = {"n": 0}

    async def fake_claude_p(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"categories": ["bug-fixing"],
                     "likely_already_satisfied": True,
                     "likely_already_satisfied_evidence": real_evidence}
        # Round 2: True again, but with empty evidence this time — an
        # invalid claim that must not clobber round 1's valid one.
        return {"categories": ["bug-fixing"],
                 "likely_already_satisfied": True,
                 "likely_already_satisfied_evidence": ""}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))
    asyncio.run(leerie.phase_classify("task", st, _caps(leerie), False, MODELS, EFFORTS))

    assert st.data["likely_already_satisfied"] is True
    assert st.data["likely_already_satisfied_evidence"] == real_evidence
