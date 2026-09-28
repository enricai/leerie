"""Tests for no_work_dispute injection into phase_plan's planner ctx
(DESIGN §8 *A dispute's evidence is planning input, not log noise*).

Motivation (measured, 2026-09-27): a run whose `no_work_judge` confirmed
every functional deliverable on HEAD and disputed solely over an unmet
standing instruction fell through to planning — and the plan carried ZERO
subtasks addressing the dispute's stated reason, re-verifying the
already-confirmed work instead. The dispute evidence was logged (200
chars) and discarded; the planner never saw it. The next run's judge was
guaranteed to dispute identically: a cross-run loop.

Coverage discipline (CLAUDE.md structure/substance pairing): every
positive test drives the REAL `phase_plan` and asserts the dispute TEXT
lands in the planner's prompt — not that a ctx key exists in a local
reproduction of the ctx-building code. The stubbed planner returns an
empty `subtasks` array, a legal outcome that skips `_recursive_decompose`
(pinned by tests/test_phase_plan_recursion_wiring.py), so no
fit_judge/splitter stubs are needed — same harness shape as
tests/test_source_of_truth_delivery.py's ctx-delivery test.
"""
from __future__ import annotations

import asyncio

MODELS = {"planner": "sonnet", "fit_judge": "sonnet", "splitter": "sonnet"}
EFFORTS = {"planner": "medium", "fit_judge": "medium", "splitter": "medium"}

_DISPUTE = {
    "classifier_evidence": "both findings already fixed on HEAD",
    "judge_evidence": (
        "functional fixes verified present; completion disputed because "
        "the delivered tests violate a standing instruction from the task"),
    "checked": ["src/example_module.py"],
}


def _state(leerie, tmp_path, **overrides):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-run-dispute-ctx"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {
        "task": "test task",
        "categories": ["testing"],
        "classifier_questions": [],
        "needs_source_of_truth": False,
        "source_of_truth_pref": "both",
        "skip_repo_map": True,
    }
    st.data.update(overrides)
    return st


def _drive_phase_plan(leerie, monkeypatch, st):
    """Run the real phase_plan with a stubbed planner; return the captured
    claude_p kwargs list."""
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"domain": "testing", "status": "ready", "subtasks": [],
                "confidence": {"task_understanding": 9.0,
                               "decomposition_quality": 9.0,
                               "basis": "stub"}}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_plan(
        "t", st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert calls, (
        "no planner was spawned — this test proves nothing unless "
        "phase_plan actually reached claude_p")
    return calls


def test_dispute_text_reaches_the_planner_prompt(
        leerie, tmp_path, monkeypatch):
    """Substance: the judge's evidence — the residual to plan — must be in
    the prompt the planner actually receives, verbatim."""
    st = _state(leerie, tmp_path, no_work_dispute=dict(_DISPUTE))
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert _DISPUTE["judge_evidence"] in prompt
    assert _DISPUTE["classifier_evidence"] in prompt
    assert '"no_work_dispute"' in prompt


def test_no_dispute_means_no_key_in_prompt(leerie, tmp_path, monkeypatch):
    """The common case (no dispute occurred) carries no false framing."""
    st = _state(leerie, tmp_path)
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "no_work_dispute" not in prompt


def test_dispute_without_judge_evidence_is_omitted(
        leerie, tmp_path, monkeypatch):
    """A record with no judge_evidence names no residual — injecting it
    would hand the planner an empty steer. The producer never writes this
    shape (it persists only on non-empty evidence); the guard covers a
    hand-edited or legacy state.json."""
    st = _state(leerie, tmp_path, no_work_dispute={
        "classifier_evidence": "claim", "judge_evidence": "", "checked": []})
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "no_work_dispute" not in prompt


def test_planner_prompt_documents_the_key(leerie):
    """The delivered signal must be documented for its consumer: a ctx key
    the planner prompt never explains is dead payload. (Pairs the
    execution tests above with the instruction side — both halves shipped
    together, DESIGN §8.)"""
    text = leerie._load_prompt("planner")
    assert "no_work_dispute" in text
    assert "judge_evidence" in text
