"""`phase_planning_coverage_gate` — advisory since 2026-08-04.

The gate used to be two-layer and terminal: a deterministic
`check_required_items_coverage` floor plus a `task_coverage_judge`, either of
which could re-drive `phase_plan` and then `die()` on exhaustion. Both layers
were demoted on measurement:

* **The floor never worked.** It required one subtask's
  `title + success_criteria_seed` tokens to be a SUPERSET of a required item's.
  Across every run that ever carried `required_items` it passed **0 of 102
  items** — 100% false positives, no true negative in its history. It also
  violated CLAUDE.md's rule that Python never infers meaning from prose, since
  `required_items` are LLM-written sentences. Deleted, not re-tuned.

* **The judge does not reproduce.** On identical input it returned a different
  finding set 85% of the time (n=20), and the intersection across repeated
  samples was **empty** — not one finding survived a re-sample.

So the gate now invokes the judge once, logs what it finds, and returns
`plans` unchanged. This file pins that contract, and pins the floor's
*absence* so it cannot silently return (the `TestRegexPathAbsent` precedent).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _subtask(sid: str, **kw) -> dict:
    d = {"id": sid, "title": f"Subtask {sid}", "success_criteria_seed": "s",
         "intent": "i", "files_likely_touched": [], "provides": [],
         "requires": [], "depends_on": []}
    d.update(kw)
    return d


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _caps(leerie) -> dict:
    return dict(leerie.DEFAULT_CAPS)


MODELS: dict[str, str] = {}
EFFORTS: dict[str, str | None] = {}

GAP = {"task_covered": False,
       "coverage_gaps": [{"kind": "missing_work", "description": "d",
                          "concrete_evidence": "e"}],
       "rationale": "r"}
CLEAN = {"task_covered": True, "coverage_gaps": [], "rationale": "r"}


@pytest.fixture
def st(leerie, tmp_path):
    s = leerie.State.__new__(leerie.State)
    s.data = {}
    s.run_dir = tmp_path
    s.save = lambda: None
    # The gate charges the per-run worker budget before invoking the judge
    # (IMPLEMENTATION.md §8: "after `st.bump_workers(caps)`"). Counting rather
    # than no-op'ing so a test can assert the charge actually happens.
    s.bumps = []
    s.bump_workers = lambda caps: s.bumps.append(caps)
    return s


def _run(leerie, plans, st):
    return asyncio.run(leerie.phase_planning_coverage_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))


# --- the floor is gone ----------------------------------------------------- #

class TestFloorDeleted:
    """Absence pin. A deleted check that quietly returns is worse than one
    never removed, because the docs will still claim it runs."""

    def test_function_is_gone_from_the_module(self, leerie):
        assert not hasattr(leerie, "check_required_items_coverage")

    def test_gate_source_does_not_reference_it(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "check_required_items_coverage(" not in src

    def test_required_items_is_still_a_state_field(self, leerie):
        """The classifier still emits it as context; nothing gates on it."""
        assert "required_items" in leerie.STATE_FIELDS


# --- advisory contract ----------------------------------------------------- #

class TestAdvisoryContract:
    def test_a_coverage_gap_does_not_die(self, leerie, st, monkeypatch):
        """THE POINT. This previously die()d after exhausting re-plans."""
        plans = [_plan("feature-implementation", _subtask("feat-001"))]

        async def fake(*a, **kw): return GAP
        monkeypatch.setattr(leerie, "claude_p", fake)
        assert _run(leerie, plans, st) is plans

    def test_a_coverage_gap_does_not_replan(self, leerie, st, monkeypatch):
        async def fake(*a, **kw): return GAP

        async def boom(*a, **k):
            pytest.fail("phase_plan must never be re-invoked by this gate")
        monkeypatch.setattr(leerie, "claude_p", fake)
        monkeypatch.setattr(leerie, "phase_plan", boom)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)

    def test_the_judge_is_invoked_exactly_once(self, leerie, st, monkeypatch):
        """A single sample. Re-invoking bought nothing — the intersection
        across repeated samples was empty."""
        calls = []

        async def fake(*a, **kw):
            calls.append(kw)
            return GAP
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert len(calls) == 1

    def test_findings_are_still_recorded(self, leerie, st, monkeypatch):
        """Advisory is not silent — #117's insight is preserved."""
        async def fake(*a, **kw): return GAP
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert st.data["coverage_gate"]["coverage_gaps"]

    def test_findings_are_logged(self, leerie, st, monkeypatch, capsys):
        async def fake(*a, **kw): return GAP
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert "advisory coverage gap" in capsys.readouterr().out

    def test_a_clean_result_passes_plans_through(self, leerie, st, monkeypatch):
        plans = [_plan("d", _subtask("feat-001"))]

        async def fake(*a, **kw): return CLEAN
        monkeypatch.setattr(leerie, "claude_p", fake)
        assert _run(leerie, plans, st) is plans

    def test_a_vague_gap_is_not_counted(self, leerie, st, monkeypatch, capsys):
        """The anti-gaming rule survives: a gap with no concrete evidence is
        not a finding."""
        async def fake(*a, **kw):
            return {"task_covered": False, "rationale": "r",
                    "coverage_gaps": [{"kind": "missing_work",
                                       "description": "d",
                                       "concrete_evidence": "  "}]}
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert "no coverage gaps reported" in capsys.readouterr().out


class TestNeverFatal:
    def test_a_judge_crash_degrades(self, leerie, st, monkeypatch):
        plans = [_plan("d", _subtask("feat-001"))]

        async def boom(*a, **kw): raise leerie.WorkerError("infrastructure")
        monkeypatch.setattr(leerie, "claude_p", boom)
        assert _run(leerie, plans, st) is plans

    def test_a_crash_writes_no_verdict(self, leerie, st, monkeypatch):
        async def boom(*a, **kw): raise leerie.WorkerError("x")
        monkeypatch.setattr(leerie, "claude_p", boom)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert "coverage_gate" not in st.data

    def test_gate_source_contains_no_die(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert "die(" not in src, "the gate must never terminate a run"


# --- skip flag ------------------------------------------------------------- #

class TestSkipCoverageCheck:
    def test_skip_short_circuits_with_zero_worker_calls(
            self, leerie, st, monkeypatch):
        st.data["skip_coverage_check"] = True
        plans = [_plan("d", _subtask("feat-001"))]

        async def fail(*a, **kw): pytest.fail("no worker when skipped")
        monkeypatch.setattr(leerie, "claude_p", fail)
        assert _run(leerie, plans, st) is plans

    def test_skip_writes_no_verdict(self, leerie, st, monkeypatch):
        st.data["skip_coverage_check"] = True

        async def fail(*a, **kw): pytest.fail("unreachable")
        monkeypatch.setattr(leerie, "claude_p", fail)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert "coverage_gate" not in st.data

    def test_gate_still_runs_when_flag_unset(self, leerie, st, monkeypatch):
        """ANTI-VACUITY: the flag must not have disabled the review."""
        calls = []

        async def fake(*a, **kw):
            calls.append(kw)
            return CLEAN
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert len(calls) == 1

    def test_short_circuit_precedes_any_work(self, leerie):
        src = inspect.getsource(leerie.phase_planning_coverage_gate)
        assert (src.index('st.data.get("skip_coverage_check")')
                < src.index("_load_prompt("))

    def test_the_flag_is_actually_seeded_on_a_fresh_run(self, leerie):
        """The producer half — which every test above is blind to.

        They all set `st.data["skip_coverage_check"]` by hand, so they pin
        the CONSUMER and pass no matter what `_run_phases` writes. And what
        it wrote, from the commit that introduced the flag (PR #162, *"add
        --skip-coverage-check, the escape hatch this gate lacked"*) until
        this one, was nothing on the fresh-run path: the key was seeded only
        under `if args.resume:`. So `.get()` returned None on every fresh
        run, the short-circuit above never fired, and the flag was silently
        inert on the path operators actually use — while this class reported
        full coverage of it.

        The general rule is derived in `test_state_fields.py`; this is the
        named pin, which fails naming this flag in the file about this gate.
        The walk is imported rather than re-implemented — see
        `tests/launcher_blocks.py` for why a shared derivation gets one
        owner.
        """
        from tests.test_state_fields import _state_init_branch_keys

        resume_keys, fresh_keys = _state_init_branch_keys(leerie)
        assert "skip_coverage_check" in resume_keys
        assert "skip_coverage_check" in fresh_keys, (
            "--skip-coverage-check is inert on every fresh run: the key is "
            "never seeded, so the short-circuit above reads None")


# --- the guard that would have caught the 0.10.0 bug ---------------------- #

class TestCallSignature:
    """Every other test in this file stubs `claude_p`, and a stub accepts any
    signature. That is precisely why 0.10.0 shipped a gate whose judge raised
    `TypeError` on every invocation — two positionals where all-keyword was
    required, and `allowed_tools`/`max_turns` (both REQUIRED) omitted — while
    a broad `except Exception` logged it as a clean advisory degrade. The
    judge never ran once.

    These bind the gate's real kwargs against the real `claude_p` signature,
    the technique `test_recursive_decompose.py`'s C0 guard already uses."""

    def _call_kwargs(self, leerie) -> dict:
        """Capture exactly what the gate passes, via a recording stub."""
        import asyncio
        captured = {}

        async def rec(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return CLEAN

        st = leerie.State.__new__(leerie.State)

        # claude_p derives the checkout write-denial from this

        # (_repo_write_denials); State.__new__ skips __init__, so it

        # must be set explicitly or both that and the §12 cwd guard

        # silently no-op.

        st.repo_root = "/leerie-test-user-repo"
        st.data = {}
        st.run_dir = "/tmp"
        st.save = lambda: None
        st.bumps = []
        st.bump_workers = lambda caps: st.bumps.append(caps)
        import unittest.mock as mock
        with mock.patch.object(leerie, "claude_p", rec):
            asyncio.run(leerie.phase_planning_coverage_gate(
                [_plan("d", _subtask("feat-001"))], "task", st,
                _caps(leerie), {}, {}))
        return captured

    def test_gate_passes_no_positional_arguments(self, leerie):
        """The shipped bug: `claude_p("task_coverage_judge", prompt, ...)`
        bound the worker name to `user_prompt` and the prompt to
        `system_prompt`, which was ALSO given by keyword."""
        cap = self._call_kwargs(leerie)
        assert cap["args"] == (), (
            f"gate passed positionals {cap['args']!r}; claude_p's first "
            "params are (user_prompt, system_prompt, ...) and every other "
            "caller uses all-keyword")

    def test_call_signature_binds_against_the_real_claude_p(self, leerie):
        """THE GUARD. Binds the captured kwargs against the REAL signature,
        so a missing required parameter fails here instead of at runtime."""
        import inspect
        cap = self._call_kwargs(leerie)
        inspect.signature(leerie.claude_p).bind(*cap["args"], **cap["kwargs"])

    def test_required_params_are_all_supplied(self, leerie):
        import inspect
        cap = self._call_kwargs(leerie)
        required = {n for n, p in inspect.signature(leerie.claude_p)
                    .parameters.items()
                    if p.default is inspect.Parameter.empty}
        missing = required - set(cap["kwargs"])
        assert not missing, f"gate omits required claude_p params: {missing}"

    def test_judge_gets_inspect_tools(self, leerie):
        """Load-bearing, not decoration: the judge's prompt instructs it to
        read task-referenced files itself. Omitting tools leaves it blind."""
        cap = self._call_kwargs(leerie)
        assert cap["kwargs"].get("allowed_tools") == leerie.INSPECT_TOOLS

    def test_model_falls_back_rather_than_passing_none(self, leerie):
        """`models.get(k)` with no default yields None, which reaches the
        argv builder and raises at subprocess spawn."""
        cap = self._call_kwargs(leerie)
        assert cap["kwargs"].get("model") is not None


class TestProgrammingErrorsPropagate:
    """A worker failure is expected here and degrades. A programming error is
    not a worker failure and must NOT be reported as an advisory degrade —
    swallowing it is what hid the broken call for a whole release."""

    def test_typeerror_propagates(self, leerie, st, monkeypatch):
        import asyncio

        async def boom(*a, **k):
            raise TypeError("bad call site")
        monkeypatch.setattr(leerie, "claude_p", boom)
        with pytest.raises(TypeError):
            _run(leerie, [_plan("d", _subtask("feat-001"))], st)

    def test_workererror_still_degrades(self, leerie, st, monkeypatch):
        """ANTI-VACUITY: narrowing the except must not make the gate fatal."""
        plans = [_plan("d", _subtask("feat-001"))]

        async def boom(*a, **k):
            raise leerie.WorkerError("infrastructure")
        monkeypatch.setattr(leerie, "claude_p", boom)
        assert _run(leerie, plans, st) is plans


class TestBudgetIsCharged:
    """IMPLEMENTATION.md §8 requires this judge be invoked "after
    `st.bump_workers(caps)`" — `integration_judge`, named in that same
    sentence, does. This gate did not, and `claude_p` does not bump
    internally, so the invocation was invisible to `max_total_workers`."""

    def test_gate_charges_exactly_one_worker(self, leerie, st, monkeypatch):
        async def fake(*a, **kw): return CLEAN
        monkeypatch.setattr(leerie, "claude_p", fake)
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert len(st.bumps) == 1, (
            f"gate charged the budget {len(st.bumps)}x; it invokes the judge "
            "exactly once")

    def test_budget_exhaustion_aborts_rather_than_degrading(
            self, leerie, st, monkeypatch):
        """The bump sits OUTSIDE the try on purpose: a budget-exhaustion
        WorkerError is the RUN being over budget, not this judge failing, so
        it must abort instead of being swallowed as an advisory degrade."""
        def boom(_caps):
            raise leerie.WorkerError("worker budget exhausted")
        st.bump_workers = boom
        with pytest.raises(leerie.WorkerError, match="budget"):
            _run(leerie, [_plan("d", _subtask("feat-001"))], st)

    def test_skip_flag_charges_nothing(self, leerie, st):
        """The cheap-skip must stay free."""
        st.data["skip_coverage_check"] = True
        _run(leerie, [_plan("d", _subtask("feat-001"))], st)
        assert st.bumps == []


class TestInfrastructureFailureDegrades:
    """The docstring's own invariant: "This gate does not terminate a run."

    An OSError from process spawn (a missing or unexecutable `claude`) is
    infrastructure, not a defect in the plan, and must not kill the run —
    every sibling advisory phase degrades on it. It is disjoint from every
    programming-error class, so admitting it re-opens nothing."""

    def test_oserror_degrades(self, leerie, st, monkeypatch):
        plans = [_plan("d", _subtask("feat-001"))]

        async def boom(*a, **k):
            raise FileNotFoundError(2, "No such file or directory", "claude")
        monkeypatch.setattr(leerie, "claude_p", boom)
        assert _run(leerie, plans, st) is plans

    def test_typeerror_still_propagates_after_the_widening(
            self, leerie, st, monkeypatch):
        """ANTI-VACUITY: admitting OSError must not re-open the TypeError
        hole that hid the broken call for a whole release."""
        async def boom(*a, **k):
            raise TypeError("bad call site")
        monkeypatch.setattr(leerie, "claude_p", boom)
        with pytest.raises(TypeError):
            _run(leerie, [_plan("d", _subtask("feat-001"))], st)

