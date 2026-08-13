"""The orchestrator measures build/lint/test; the conformer consumes results.

DESIGN §9. Two levers replace the old prompt contract, and both are code:

1. the command strings are no longer injected, so running a full axis now
   requires the worker to synthesise one;
2. the orchestrator's measurement OVERWRITES the worker's self-reported axes
   before anything reads them, so what the conformer claims about
   build/lint/test stops being load-bearing anywhere.

Note the measured context: conformers were NOT over-firing (219 full-suite
runs across 229 calls, ~1.0 each, exactly the old prompt contract). The
handover is not a discipline fix — it is what makes scoping and memoising
possible at all.
"""
from __future__ import annotations

import ast
import inspect
import io
import pathlib
import tokenize

import pytest


def _strip_comments(src: str) -> str:
    """Remove comments before scanning.

    The regions below necessarily MENTION `TEST_CMD` and `BLT_RESULTS` while
    explaining the change, so a raw substring scan matches the prose
    describing what it forbids — the same trap `tests/test_subreaper.py`
    documents for its `/proc` guard.
    """
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok)
    return tokenize.untokenize(out)


def _prompt(name: str, leerie) -> str:
    return (pathlib.Path(leerie.__file__).resolve().parent.parent
            / "prompts" / f"{name}.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Lever 1: the command strings are gone from both call sites
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", ["_run_conformer", "_run_final_conformance"])
def test_no_command_strings_are_injected(leerie, fn_name):
    src = _strip_comments(inspect.getsource(getattr(leerie, fn_name)))
    for marker in ("BUILD_CMD", "LINT_CMD", "TEST_CMD"):
        assert marker not in src, (
            f"{fn_name} still hands the conformer a raw {marker} — the "
            "worker can then run a full axis without synthesising anything")


@pytest.mark.parametrize("fn_name", ["_run_conformer", "_run_final_conformance"])
def test_the_results_block_is_injected_instead(leerie, fn_name):
    """ANTI-VACUITY PARTNER for the absence test: an absence-only assertion
    passes against a function that injects nothing at all."""
    src = _strip_comments(inspect.getsource(getattr(leerie, fn_name)))
    assert "_format_blt_results_section" in src


def test_the_prompt_stops_asking_the_worker_to_run_the_axes(leerie):
    txt = _prompt("conformer", leerie)
    assert "BLT_RESULTS" in txt
    assert "full (un-scoped)" not in txt, (
        "the old once-per-round run instruction survives in the prompt")


# --------------------------------------------------------------------------
# Lever 2: the overwrite — behavioural, not just structural
# --------------------------------------------------------------------------

def test_measurement_overwrites_the_workers_self_report(leerie):
    """THE LOAD-BEARING TEST. A conformer claiming green while the
    orchestrator measured red must not be believed."""
    claimed = {"tests": {"ran": True, "measured": True, "passed": True,
                         "command": "vitest run", "summary": "all good"},
               "rule_violations_residual": []}
    measured = {"tests": {"ran": True, "measured": True, "passed": False,
                          "command": "vitest run", "summary": "2 failed"}}
    out = leerie._apply_measured_axes(claimed, measured)
    assert out["tests"]["passed"] is False
    assert out["tests"]["summary"] == "2 failed"


def test_the_overwrite_does_not_mutate_the_raw_worker_output(leerie):
    """The raw payload is persisted as telemetry and must stay as-emitted —
    the same discipline `_expand_conformer_output` follows."""
    claimed = {"tests": {"ran": True, "measured": True, "passed": True}}
    leerie._apply_measured_axes(claimed, {"tests": {"passed": False}})
    assert claimed["tests"]["passed"] is True


def test_the_overwrite_runs_before_any_gate_that_can_break(leerie):
    """The apply must precede the gate section, not merely `_conformance_clean`.

    This replaces a source-INDEX comparison
    (`src.index("_apply_measured_axes") < src.index("_conformance_clean")`)
    that could not fail: the tail apply satisfied it while three gates —
    malformed result, protected-path violation, strict-mode clobber — `break`
    out of the round *before* reaching it, carrying the worker's self-reported
    axes into `_summarize_residuals`, the persisted `conformance` entry, and
    the strict post-loop `_conformance_clean`. A position check cannot see a
    path that jumps over the position. The behavioural proof lives in
    `tests/test_run_conformance_phase.py`; this pins the structure.
    """
    for fn in (leerie._run_conformance_phase, leerie._run_final_conformance):
        src = _strip_comments(inspect.getsource(fn))
        first_apply = src.index("_apply_measured_axes")
        for gate in ("_validate_conformance_result", "check_diff_scope",
                     "_clobbered_owned_files"):
            if gate in src:
                assert first_apply < src.index(gate), (
                    f"{fn.__name__}: {gate} can break out of the round before "
                    "the measurement overwrites the worker's axes")


def test_the_overwrite_is_applied_twice_per_round(leerie):
    """Once before the gates (from `pre`, which describes the tree a rollback
    path leaves behind) and once at the tail (from `post`). Dropping either
    re-opens a path on which claimed axes reach a consumer."""
    for fn in (leerie._run_conformance_phase, leerie._run_final_conformance):
        src = _strip_comments(inspect.getsource(fn))
        assert src.count("_apply_measured_axes") == 2, (
            f"{fn.__name__} should apply the measurement twice per round")


def test_an_empty_measurement_leaves_the_result_alone(leerie):
    """`--subtask-tests off` measures nothing; the worker's own report is
    then all there is, and must survive."""
    claimed = {"tests": {"ran": True, "passed": True}}
    assert leerie._apply_measured_axes(claimed, {}) is claimed


# --------------------------------------------------------------------------
# Ordering: measure, spawn, measure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("fn_name", ["_run_conformance_phase",
                                     "_run_final_conformance"])
def test_measures_before_and_after_the_round(leerie, fn_name):
    src = _strip_comments(inspect.getsource(getattr(leerie, fn_name)))
    assert src.count("_measure_axes") >= 1 or "_measure_final" in src
    assert "_round_axis_regressions" in src


def test_the_results_block_is_built_from_the_pre_measurement(leerie):
    """Handing the conformer the POST measurement would describe a tree it
    has not yet acted on."""
    src = _strip_comments(inspect.getsource(leerie._run_conformance_phase))
    assert "blt_results=pre" in src


# --------------------------------------------------------------------------
# The final pass never narrows
# --------------------------------------------------------------------------

def test_the_final_pass_uses_canonical_commands_only(leerie):
    """DESIGN §6: the final pass exists for cross-subtask interaction
    breakage — a lint rule sensitive to file count, an import collision that
    compiled cleanly in isolation — none of which a diff-scoped selection can
    see."""
    src = _strip_comments(inspect.getsource(leerie._run_final_conformance))
    assert "_select_subtask_axes" not in src
    assert "resolve_blt_scoped" not in src
    assert '_format_blt_results_section(pre, "full")' in src


# --------------------------------------------------------------------------
# The knob is wired end to end
# --------------------------------------------------------------------------

def test_subtask_tests_is_seeded_on_both_run_init_branches(leerie):
    """A resume-only seed is the `skip_coverage_check` defect: the consumer
    reads `st.data`, so the flag was silently inert on every fresh run while
    its own tests (which set the key by hand) reported full coverage.

    Uses the shared walk rather than a local one —
    `tests/test_no_duplicate_state_walks.py` enforces a single owner, and for
    a sharper reason than tidiness: a drifted second copy under-reports the
    resume branch, which makes the symmetry guard pass *vacuously* instead of
    failing.

    **This proves presence, not evaluation, and cannot be made to.** It
    passed against the v0.20.0 build whose fresh-branch value expression was
    `resolve_subtask_tests(repo_root, ...)` with `repo_root` bound nowhere —
    the key was in the literal; reading it raised `NameError` and killed
    every fresh run. Execution coverage for that branch lives in
    `tests/test_run_phases_fresh_init.py`; the whole-module generalisation is
    `tests/test_no_undefined_names.py`.
    """
    from tests.test_state_fields import _state_init_branch_keys
    resume_keys, fresh_keys = _state_init_branch_keys(leerie)
    assert "subtask_tests" in resume_keys, "not seeded on the resume branch"
    assert "subtask_tests" in fresh_keys, "not seeded on the fresh branch"


def test_the_phase_reads_the_knob(leerie):
    src = _strip_comments(inspect.getsource(leerie._run_conformance_phase))
    assert 'st.data.get("subtask_tests")' in src


def test_resolver_default_and_values(leerie, tmp_path):
    assert leerie.resolve_subtask_tests(tmp_path, None) == "scoped"
    assert leerie.resolve_subtask_tests(tmp_path, "full") == "full"
    assert leerie.SUBTASK_TESTS_VALUES == ("scoped", "full", "off")
