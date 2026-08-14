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


def _strip_comments(src: str) -> str:
    """Source with `#` comments removed, via `tokenize`.

    Not a `#`-prefix line heuristic: a `#` inside a string literal would
    corrupt the result. Needed because the comments in the region under test
    necessarily *name* the calls being counted — scanning raw source finds the
    prose describing the rule as though it were the rule.
    """
    out, last = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] > last[0]:
            out.append("\n" * (tok.start[0] - last[0]))
            last = (tok.start[0], 0)
        out.append(" " * max(0, tok.start[1] - last[1]) + tok.string)
        last = tok.end
    return "".join(out)

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
    def _guarded_arms(self, leerie) -> list[str]:
        """Every `except (...)` tuple guarding a best-effort capture in a
        terminal arm of `main()` or `phase_finalize`.

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
            if node.name not in ("main", "phase_finalize"):
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

    def test_the_demotion_runs_the_repo_preflight(self, leerie):
        """`main()` gates `_preflight_repo()` on `if not args.resume:`, and
        this demotion happens long after that gate was passed. Without an
        explicit call here, a demoted resume starts real work as a fresh run
        having skipped git-identity, dirty-tree and branch-collision — checks
        that live ONLY in `_preflight_repo()` since the split, so the later
        `preflight()` does not cover them.
        """
        src = inspect.getsource(leerie._run_phases)
        i = src.index("args.resume = False")
        window = src[i:i + 900]
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


class TestAFailedStartLeavesTheRunRestartable:
    """`die()` before any work must not stamp the run terminal.

    `main()`'s SystemExit handler writes `finished_at`, which is read as a
    TERMINAL state in three places: the never-started demotion excludes it,
    `_derive_run_status` reports `done` (so bare `resume` will not auto-pick
    it), and an explicit `resume` then falls through to "records neither
    progress nor a task" — the message POSTMORTEM F15 records as sending an
    operator hunting a data-integrity problem that did not exist.

    So a run that dies in preflight became permanently dead. The likeliest
    trigger is a dirty working tree, which is precisely the condition `main()`
    deliberately declines to re-check on an ordinary resume — and the demotion
    added a new, far more reachable, refusal on that path.
    """

    @staticmethod
    def _handler(leerie) -> str:
        src = inspect.getsource(leerie.main)
        i = src.index("except SystemExit")
        return src[i:src.index("\n    except ", i + 10)]

    def test_a_never_started_run_is_not_stamped_finished(self, leerie):
        h = _strip_comments(self._handler(leerie))
        assert "_never_started" in h, (
            "the handler must distinguish a run that never started from one "
            "that finished")
        # Structural, not whitespace-exact: the guard must precede the stamp.
        guard = h.index("if not _never_started:")
        stamp = h.index('st.data["finished_at"] = now()')
        assert guard < stamp, (
            "the stamp must sit inside the not-never-started branch")

    def test_the_predicate_matches_the_demotion(self, leerie):
        """Both must mean the same thing by 'never started', or a run can be
        stamped terminal by one and expected restartable by the other."""
        h = _strip_comments(self._handler(leerie))
        for token in ('"waves" not in st.data', '"categories" not in st.data',
                      'st.data.get("task")'):
            assert token in h, token

    def test_run_json_is_not_stamped_either(self, leerie):
        """`_derive_run_status` reads run.json, so stamping only state.json
        would leave the run reporting `done` to `leerie list`."""
        h = _strip_comments(self._handler(leerie))
        i = h.index("_write_run_json(")
        assert "if not _never_started:" in h[max(0, i - 200):i]

    def test_a_run_that_DID_work_is_still_stamped(self, leerie):
        """Anti-vacuity. `fetch_branch`'s discovery requires `finished_at` on
        a completed unpushed run; suppressing it unconditionally would make
        every real post-setup die() undiscoverable — the defect the stamp was
        added for."""
        h = _strip_comments(self._handler(leerie))
        assert 'st.data["finished_at"] = now()' in h, (
            "the stamp must still happen for a run with progress")

    def test_status_of_a_never_started_run_is_resumable(self, leerie):
        """End-to-end on the consumer: no finished_at means not `done`, and
        `resume` can auto-pick it again."""
        run_json = {"run_id": "r", "started_at": "t"}     # no finished_at
        state = {"task": "t", "started_at": "t"}           # no waves/categories
        status = leerie._derive_run_status(run_json, state)
        assert status != "done"
        assert status in leerie._AUTO_RESUMABLE_STATUSES, status

    def test_a_stamped_run_really_would_be_done(self, leerie):
        """Anti-vacuity for the test above: `finished_at` IS what makes it
        terminal, so the fix is load-bearing rather than incidental."""
        stamped = leerie._derive_run_status(
            {"run_id": "r", "started_at": "t", "finished_at": "t"},
            {"task": "t", "started_at": "t"})
        assert stamped == "done"
        assert stamped not in leerie._AUTO_RESUMABLE_STATUSES
