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
import io
import tokenize
from pathlib import Path

import pytest
from tests.source_strip import strip_comments as _strip_comments   # single owner; see that module


_ORCH = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"


def _main_src(leerie) -> str:
    return inspect.getsource(leerie.main)


class TestRepoChecksPrecedeTheRunDirectory:
    def test_preflight_repo_owns_all_three_checks(self, leerie):
        """Not merely that it exists — existence-only pins guarantee a helper
        stays present, possibly dead. What matters is that it is the one place
        the three repo checks live, which is also what makes
        `test_the_repo_checks_moved_out_of_preflight` meaningful."""
        src = inspect.getsource(leerie._preflight_repo)
        assert "user.email" in src and "user.name" in src
        assert "modified/staged file(s)" in src
        assert "is not configured" in src

    def test_it_runs_before_State_is_constructed(self, leerie):
        """Ordering IS the fix — a behavioural test cannot see it."""
        src = _main_src(leerie)
        pre = src.index("_preflight_repo()")
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
        early = inspect.getsource(leerie._preflight_repo)
        assert "modified/staged file(s)" in early
        assert "is not configured" in early

    def test_resume_skips_them(self, leerie):
        """A resumed run's directory already exists, so there is nothing left
        to protect — and re-checking would refuse a resume over a dirty tree
        the operator may be mid-way through fixing."""
        src = _main_src(leerie)
        i = src.index("_preflight_repo()")
        window = src[max(0, i - 400):i]
        assert "not args.resume" in window, window[-200:]

    def test_the_dirty_tree_message_names_the_files(self, leerie):
        src = inspect.getsource(leerie._preflight_repo)
        assert "_shown" in src and "join(_shown)" in src, (
            "the message must list the offending paths — '2 modified/staged "
            "file(s)' sends the operator to `git status` to find out what "
            "leerie is objecting to")
        assert "belong to leerie itself" in src, (
            "when the dirty files are leerie's OWN config, say so: that was "
            "the measured case, and nothing connected the two")


class TestTerminalArmsSurviveTheirOwnInterrupt:
    # The functions that may hold a direct `try:` around `capture_repo_deps`.
    # `_best_effort_capture_deps` (refactor-001) is the single owner of the
    # guard that main()'s six terminating arms previously hand-rolled inline;
    # they now delegate to it, so it must be scanned alongside the two
    # arm-holding functions or the sweep goes blind to the very guard it
    # exists to protect. `test_every_main_capture_goes_through_the_guard`
    # below closes the corresponding hole: an arm that called
    # capture_repo_deps WITHOUT the helper (and without an inline try) is the
    # unguarded escape the collapse could otherwise hide.
    _GUARD_FUNCS = ("main", "phase_finalize", "_best_effort_capture_deps")

    def _guarded_arms(self, leerie) -> list[str]:
        """Every `except (...)` tuple guarding a best-effort capture — the
        inline arms of `main()`/`phase_finalize` plus the shared
        `_best_effort_capture_deps` helper the collapsed arms delegate to.

        Selected by what the guarded `try` DOES — it calls `capture_repo_deps`
        — not by which exception names the tuple happens to contain. The first
        version required both `TerminalAuthFailure` and `DiskLowSpace`, so a
        newly added terminal arm guarded by a different tuple was invisible to
        the sweep below, which is precisely the arm most likely to have been
        written without `KeyboardInterrupt`.
        """
        out = []
        tree = ast.parse(_ORCH.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in self._GUARD_FUNCS:
                continue
            for t in ast.walk(node):
                if not isinstance(t, ast.Try):
                    continue
                body = "".join(ast.unparse(s) for s in t.body)
                if "capture_repo_deps" not in body:
                    continue
                for h in t.handlers:
                    if isinstance(h.type, ast.Tuple):
                        out.append(sorted(ast.unparse(e) for e in h.type.elts))
                    elif h.type is not None:
                        out.append([ast.unparse(h.type)])
        return out

    def test_the_scan_finds_them(self, leerie):
        """Anti-vacuity: a scan matching nothing passes the next test."""
        # After refactor-001 collapsed main()'s six inline arms into
        # `_best_effort_capture_deps`, the direct-try guards left are that
        # helper plus phase_finalize's own inline arm — two. A floor well
        # below reality is a weak control, so this is pinned at exactly what
        # the current structure yields; a new arm that hand-rolls its own
        # guard raises it, and the KeyboardInterrupt sweep below still covers
        # every arm the scan finds.
        arms = self._guarded_arms(leerie)
        assert len(arms) >= 2, arms

    def test_every_one_catches_KeyboardInterrupt(self, leerie):
        offenders = [n for n in self._guarded_arms(leerie)
                     if "KeyboardInterrupt" not in n]
        assert not offenders, (
            "a terminal arm exists to record a disposition; a Ctrl-C during "
            "its best-effort capture must not escape main() and skip the "
            f"exit_code and cleanup that arm was reached to set: {offenders}")

    def test_every_main_capture_goes_through_the_guard(self, leerie):
        """Every `capture_repo_deps` call inside a `main()` terminal arm must
        be routed through the guarded `_best_effort_capture_deps` helper.

        After refactor-001 the arms no longer carry their own try/except, so
        the KeyboardInterrupt sweep above would not see an arm that called
        `capture_repo_deps` BARE — the exact unguarded escape the collapse
        could hide. Enforce it structurally: in `main()`, `capture_repo_deps`
        may appear only as an argument to `_best_effort_capture_deps` (i.e.
        never as its own call target)."""
        tree = ast.parse(_ORCH.read_text())
        main_node = next(
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "main")
        bare_calls = [
            ast.unparse(c) for c in ast.walk(main_node)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "capture_repo_deps"
        ]
        assert not bare_calls, (
            "main() must route best-effort capture through "
            "_best_effort_capture_deps (which carries the non-fatal "
            f"try/except), never call capture_repo_deps directly: {bare_calls}")


class TestNeverStartedIsRestartable:
    def test_the_corruption_claim_is_gone(self, leerie):
        """Comments stripped: a comment recording the retired wording — the
        natural place to explain why it went — would otherwise fail this on
        correct code."""
        src = _strip_comments(inspect.getsource(leerie._run_phases))
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

    def test_finished_at_is_deliberately_not_a_disqualifier(self, leerie):
        """`main()` stamps `finished_at` on EVERY die(), including one that
        fired before the run did any work — so excluding it made a run that
        died in preflight permanently unrestartable.

        Suppressing the stamp instead was tried and reverted: it left the run
        with no terminal marker at all, so `_live_duplicate_runs` read it as
        LIVE forever and refused every re-run of the same task, naming a run
        that was not running. The stamp stays; the demotion tolerates it.
        """
        src = _strip_comments(inspect.getsource(leerie._run_phases))
        i = src.index("args.resume = False")
        window = src[max(0, i - 1200):i]
        assert "finished_at" not in window, (
            "excluding finished_at is what made a failed start unrecoverable")

    def test_a_completed_run_is_still_not_demoted(self, leerie):
        """The conjunct that actually does the work. A completed run has
        `waves`, so it fails `"waves" not in st.data` before anything else is
        consulted — which is why dropping the finished_at test is safe."""
        src = _strip_comments(inspect.getsource(leerie._run_phases))
        i = src.index("args.resume = False")
        window = src[max(0, i - 1200):i]
        assert '"waves" not in st.data' in window
        assert '"categories" not in st.data' in window

    def test_a_no_work_run_is_still_not_demoted(self, leerie):
        """`_finish_no_work_run` sets `waves = []`, so the waves conjunct
        already excludes it; the explicit test is belt-and-braces."""
        assert 'st.data["waves"] = []' in inspect.getsource(
            leerie._finish_no_work_run)
        src = _strip_comments(inspect.getsource(leerie._run_phases))
        i = src.index("args.resume = False")
        assert "no_work_required" in src[max(0, i - 1200):i]

    def test_no_task_at_all_still_dies(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "neither progress nor a" in src, (
            "the one genuinely unusable state must still die")

    def test_the_demotion_runs_the_repo_preflight(self, leerie):
        """`main()` gates `_preflight_repo()` on `if not args.resume:`, and
        this demotion happens long after that gate was passed. Without an
        explicit call here, a demoted resume starts real work as a fresh run
        having skipped git-identity, dirty-tree and branch-collision — checks
        that live ONLY in `_preflight_repo()` since the split, so the later
        `preflight()` does not cover them.
        """
        # Structural slice, not a character count. This was `src[i:i + 900]`
        # with the call sitting at 850 — fifty characters of headroom, so one
        # more comment line in that block would have failed it on correct code.
        src = inspect.getsource(leerie._run_phases)
        i = src.index("args.resume = False")
        window = src[i:src.index("\n    if args.resume:", i)]
        assert "await _preflight_repo()" in window, (
            "the demoted resume is a fresh start and must be preflighted "
            "like one")

    def test_an_ordinary_resume_still_skips_the_repo_preflight(self, leerie):
        """Anti-vacuity: the call must sit inside the demotion branch, not at
        the top of `_run_phases` where it would refuse every resume over a
        dirty tree the operator may be mid-way through fixing."""
        # Comments stripped first: the ones around the call necessarily name
        # `_preflight_repo()` while explaining why it is there, so a raw scan
        # counts the prose describing the rule as instances of the rule.
        src = _strip_comments(inspect.getsource(leerie._run_phases))
        assert src.count("_preflight_repo()") == 1, (
            "exactly one call, and it belongs to the demotion branch")
        call = src.index("_preflight_repo()")
        demote = src.index("args.resume = False")
        assert demote < call, (
            "preflighting before the demotion decision would catch every "
            "resume, not just the one that becomes a fresh start")


class TestTheDemotionCannotRestartFinishedWork:
    """The demotion widened its predicate to tolerate `finished_at`, which
    `main()` stamps on every die(). That removed the conjunct that was
    *implicitly* keeping the completed-run and no-work early returns reachable
    — they sit AFTER this block, so a completed run whose state carries no
    `waves` was demoted and restarted.

    `current_phase` is the precise replacement: stamped at every phase entry
    and documented as "Empty string before phase 1", so its absence IS "this
    run never started".
    """

    def test_the_predicate_requires_no_phase_was_entered(self, leerie):
        src = _strip_comments(inspect.getsource(leerie._run_phases))
        i = src.index("args.resume = False")
        window = src[max(0, i - 1400):i]
        assert 'not st.data.get("current_phase")' in window

    def test_current_phase_is_documented_as_empty_before_phase_one(self, leerie):
        """The predicate is only correct if the field means what it says."""
        i = leerie.STATE_FIELDS.index("current_phase")
        src = inspect.getsource(leerie)
        j = src.index('"current_phase"')
        assert "Empty string before phase 1" in src[max(0, j - 400):j], (
            "the demotion relies on this documented meaning")
        assert i >= 0
