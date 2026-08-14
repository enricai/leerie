"""Validate before committing irreversible state; survive your own exception.

Three faces of one class, all measured in the same corpus
(docs/POSTMORTEM-2026-08-14.md, F14 and F15):

  * `main()` constructed `State(...)` — which mints `<state-root>/runs/<run-id>/`,
    takes its flock and writes `state.json` — and only then reached the
    repo-state checks. Four permanent run directories accumulated on one host
    from a single afternoon of dirty-tree refusals, indistinguishable in
    `leerie list` from runs that did work.
  * the terminal arms guarded their best-effort `capture_repo_deps` with a
    tuple that omitted `KeyboardInterrupt` — the one exception guaranteed to be
    in flight when the arm IS a SIGINT handler — so a second Ctrl-C escaped
    `main()` and skipped the `exit_code`/cleanup that arm was reached to set.
  * `resume` called a run that was merely interrupted before `phase_classify`
    "likely corrupt or hand-edited", sending an operator after a
    data-integrity problem that did not exist.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"


def _main_src(leerie) -> str:
    return inspect.getsource(leerie.main)


class TestRepoChecksPrecedeTheRunDirectory:
    def test_preflight_repo_exists_and_is_separate(self, leerie):
        assert callable(leerie.preflight_repo)

    def test_it_runs_before_State_is_constructed(self, leerie):
        """Ordering IS the fix — a behavioural test cannot see it."""
        src = _main_src(leerie)
        pre = src.index("preflight_repo()")
        state = src.index("st = State(leerie_root, run_id")
        assert pre < state, (
            "repo-state checks must run before State(...) mints the run "
            "directory, takes its flock and writes state.json")

    def test_the_repo_checks_moved_out_of_preflight(self, leerie):
        """Anti-vacuity: they must not ALSO still run late.

        If `preflight()` kept them, the early call would be decorative and a
        refusal would still leave a run directory behind.
        """
        late = inspect.getsource(leerie.preflight)
        assert "modified/staged file(s)" not in late
        assert "is not configured" not in late
        early = inspect.getsource(leerie.preflight_repo)
        assert "modified/staged file(s)" in early
        assert "is not configured" in early

    def test_resume_skips_them(self, leerie):
        """A resumed run's directory already exists, so there is nothing left
        to protect — and re-checking would refuse a resume over a dirty tree
        the operator may be mid-way through fixing."""
        src = _main_src(leerie)
        i = src.index("preflight_repo()")
        window = src[max(0, i - 400):i]
        assert "not args.resume" in window, window[-200:]

    def test_the_dirty_tree_message_names_the_files(self, leerie):
        src = inspect.getsource(leerie.preflight_repo)
        assert "_shown" in src and "join(_shown)" in src, (
            "the message must list the offending paths — '2 modified/staged "
            "file(s)' sends the operator to `git status` to find out what "
            "leerie is objecting to")
        assert "belong to leerie itself" in src, (
            "when the dirty files are leerie's OWN config, say so: that was "
            "the measured case, and nothing connected the two")


class TestTerminalArmsSurviveTheirOwnInterrupt:
    def _guarded_arms(self, leerie) -> list[str]:
        """Every `except (...)` tuple guarding a best-effort capture in a
        terminal arm of `main()` or `phase_finalize`."""
        out = []
        tree = ast.parse(_ORCH.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in ("main", "phase_finalize"):
                continue
            for h in ast.walk(node):
                if not isinstance(h, ast.ExceptHandler):
                    continue
                if not isinstance(h.type, ast.Tuple):
                    continue
                names = {ast.unparse(e) for e in h.type.elts}
                if "TerminalAuthFailure" in names and "DiskLowSpace" in names:
                    out.append(sorted(names))
        return out

    def test_the_scan_finds_them(self, leerie):
        """Anti-vacuity: a scan matching nothing passes the next test."""
        assert len(self._guarded_arms(leerie)) >= 4, self._guarded_arms(leerie)

    def test_every_one_catches_KeyboardInterrupt(self, leerie):
        offenders = [n for n in self._guarded_arms(leerie)
                     if "KeyboardInterrupt" not in n]
        assert not offenders, (
            "a terminal arm exists to record a disposition; a Ctrl-C during "
            "its best-effort capture must not escape main() and skip the "
            f"exit_code and cleanup that arm was reached to set: {offenders}")


class TestNeverStartedIsRestartable:
    def test_the_corruption_claim_is_gone(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "likely corrupt or was hand-edited" not in src, (
            "a run interrupted before phase_classify is not corrupt")

    def test_a_recorded_task_demotes_the_resume_to_a_start(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "args.resume = False" in src
        i = src.index("args.resume = False")
        assert "args.task = st.data[\"task\"]" in src[max(0, i - 400):i + 200], (
            "the fresh-run branch reads the task from argv and a resume "
            "invocation carries none; without handing it the recorded task "
            "the demotion trades one unhelpful die() for another")

    def test_the_demotion_precedes_the_dispatch(self, leerie):
        """Flipping the flag inside the resume arm looks equivalent and is not:
        the `else:` has already been skipped, so `st.data` is never seeded and
        the run dies later on a missing `started_at`."""
        src = inspect.getsource(leerie._run_phases)
        demote = src.index("args.resume = False")
        dispatch = src.index("\n    if args.resume:\n", demote)
        assert demote < dispatch

    @pytest.mark.parametrize("terminal_key", ["finished_at", "no_work_required"])
    def test_terminated_runs_are_not_demoted(self, leerie, terminal_key):
        """A completed or no-work run also lacks `waves`, and both have their
        own early returns. Demoting them would restart finished work."""
        src = inspect.getsource(leerie._run_phases)
        i = src.index("args.resume = False")
        window = src[max(0, i - 900):i]
        assert terminal_key in window, (
            f"the never-started predicate must exclude {terminal_key}")

    def test_no_task_at_all_still_dies(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "neither progress nor a" in src, (
            "the one genuinely unusable state must still die")
