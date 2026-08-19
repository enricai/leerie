"""Behavioural coverage for `_run_phases`' tail: `phase_execute` ->
`_run_final_conformance` (wrapped in an advisory try/except) ->
`phase_finalize` (orchestrator/leerie.py, end of `_run_phases`).

No existing test drives this far. Every harness that reaches `_run_phases`
stops the run early via a stubbed `phase_execute` that raises a sentinel
(see tests/test_resume_planning_reentry.py and its importers,
tests/test_wiring_gate_resume.py, tests/test_checkpoint_aliasing.py). The
comment at the try/except site says "`_run_final_conformance` is
documented to never raise" — this file pins the defense-in-depth wrapper
around it anyway, and the unconditional `phase_finalize` call that
follows, by actually letting `phase_execute` return instead of raising.

`_run_final_conformance` and `phase_finalize` are themselves stubbed: this
file is about the GLUE in `_run_phases`, not about either phase's own
internals (covered elsewhere — tests/test_run_final_conformance.py,
tests/test_phase_finalize_*.py).
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_resume_planning_reentry import (
    EFFORTS, MODELS, _args, _caps, _make_state, _stub_common,
    run_dirs,  # noqa: F401  — pytest fixture, imported for use
)
from tests.test_wiring_gate_resume import PLANS, SNAPSHOT


def _seeded(**extra) -> dict:
    """state.json with every planning checkpoint present, including
    `plan_snapshot` and a clean-pass `wiring_gate` — so `_run_phases`
    rehydrates straight through the planning pipeline with no LLM calls
    and reaches `phase_execute` immediately."""
    data = {
        "task": "t",
        "categories": ["refactoring"],
        "answers": {"source_of_truth": "codebase"},
        "plan_snapshot": SNAPSHOT,
        "wiring_gate": {"wiring_defects": []},
    }
    for phase in ("classify", "plan", "reconcile", "overlap_judge",
                  "adherence_gate", "coverage_gate", "filters"):
        data[f"plans_after_{phase}"] = PLANS
    data.update(extra)
    return data


def _drive_to_finalize(leerie, monkeypatch, run_dirs, *, conformance_raises):
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)

    async def _execute(*a, **kw):
        calls["phase_execute"] = calls.get("phase_execute", 0) + 1
    monkeypatch.setattr(leerie, "phase_execute", _execute)

    async def _final_conformance(*a, **kw):
        calls["_run_final_conformance"] = calls.get(
            "_run_final_conformance", 0) + 1
        if conformance_raises:
            raise RuntimeError("boom from final conformance")
    monkeypatch.setattr(leerie, "_run_final_conformance", _final_conformance)

    finalize_kwargs: dict = {}

    async def _finalize(leerie_dir, st, **kwargs):
        calls["phase_finalize"] = calls.get("phase_finalize", 0) + 1
        finalize_kwargs.update(kwargs)
    monkeypatch.setattr(leerie, "phase_finalize", _finalize)

    st = _make_state(leerie, run_dirs, _seeded())
    leerie_root, run_id, run_dir = run_dirs
    asyncio.run(leerie._run_phases(
        _args(no_push=True, no_verify=True, pr_template="tmpl",
              host_no_push=False),
        _caps(leerie), run_dir, st, "codebase", "normal", MODELS, EFFORTS))
    return st, calls, finalize_kwargs


def test_final_conformance_exception_is_swallowed_and_recorded(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """`_run_final_conformance` is documented to never raise, but the
    defense-in-depth wrapper around it must still catch the exception, log
    it, record it as an advisory conformance warning, and — the whole
    point of the arm — still let `phase_finalize` run."""
    st, calls, _kw = _drive_to_finalize(
        leerie, monkeypatch, run_dirs, conformance_raises=True)

    assert calls.get("_run_final_conformance") == 1
    assert calls.get("phase_finalize") == 1, (
        "a crash in the advisory final-conformance pass must not block "
        "phase_finalize")
    warnings = st.data["conformance"]["_final"]["warnings"]
    assert any("RuntimeError" in w and "boom from final conformance" in w
               for w in warnings)


def test_final_conformance_success_reaches_finalize_with_no_fabricated_warning(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """Anti-vacuity control: the healthy (non-raising) path also reaches
    phase_finalize, without fabricating a conformance warning it never
    earned — proving the warning above comes from the except arm, not from
    some unconditional side effect of driving the harness."""
    st, calls, _kw = _drive_to_finalize(
        leerie, monkeypatch, run_dirs, conformance_raises=False)

    assert calls.get("_run_final_conformance") == 1
    assert calls.get("phase_finalize") == 1
    assert "_final" not in st.data.get("conformance", {})


def test_phase_finalize_receives_the_run_flags_from_args(
        leerie, monkeypatch, run_dirs):  # noqa: F811
    """The four `getattr(args, ..., default)` reads at the `phase_finalize`
    call site must actually reach it with the values `args` carries."""
    _st, _calls, kwargs = _drive_to_finalize(
        leerie, monkeypatch, run_dirs, conformance_raises=False)

    assert kwargs["no_push"] is True
    assert kwargs["no_verify"] is True
    assert kwargs["pr_template_override"] == "tmpl"
    assert kwargs["host_no_push"] is False
