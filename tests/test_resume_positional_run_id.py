"""`leerie resume <run-id>` must resume THAT run, not auto-pick another.

The documented interface (CLAUDE.md Quick start: `./leerie resume <run-id>`)
silently ignored the run-id on every runtime. `main()` popped only `argv[0]`
(the verb), so the run-id in `argv[1]` bound to argparse's `task` positional,
`args.run_id` stayed `None`, and `resolve_run_id` auto-picked the newest
resumable run instead.

Measured 2026-08-05: `leerie resume 488c42e5…` announced

    auto-picked the most recent resumable run 7859ad30… (in-progress)

— a **live** run owned by another orchestrator. Only the flock stopped a
duplicate (exit 75). An *idle* auto-picked run would have been resumed
silently, spending hours on the wrong work.

`resume` is the only verb affected: `stop` / `kill` / `accept-blocked` /
`finalize` / `status` all `exit` inside the launcher and never reach this
argparse.

**Why nothing caught it:** `test_resolve_run_id.py` and
`test_resolve_run_id_autopick.py` call `resolve_run_id(...)` directly *with* a
run-id, so they pass against the broken plumbing. Nothing crossed the
launcher→argparse boundary — the same shape as the 0.10.0 coverage-gate bug,
where every test stubbed `claude_p` and so no test could see a broken call.
"""
from __future__ import annotations

import ast
import inspect


class TestExtractsThePositional:
    def test_run_id_is_taken_off_the_front(self, leerie):
        run_id, rest = leerie._extract_resume_run_id(
            ["488c42e5", "--max-workers", "1200"])
        assert run_id == "488c42e5"
        assert rest == ["--max-workers", "1200"], (
            "the run-id must be REMOVED, or argparse still binds it to `task`")

    def test_bare_resume_is_untouched(self, leerie):
        """Auto-pick is correct when no id is given — that behaviour stays."""
        assert leerie._extract_resume_run_id([]) == (None, [])
        assert leerie._extract_resume_run_id(["--report"]) == (
            None, ["--report"])

    def test_flag_form_is_left_for_argparse(self, leerie):
        """`resume --run-id X` already worked; it must keep working through
        argparse rather than being intercepted here."""
        run_id, rest = leerie._extract_resume_run_id(["--run-id", "X"])
        assert run_id is None
        assert rest == ["--run-id", "X"]

    def test_a_flag_value_is_never_mistaken_for_the_run_id(self, leerie):
        """Only argv[0] is considered, and only when it is not a flag."""
        assert leerie._extract_resume_run_id(
            ["--max-workers", "1200", "abc"])[0] is None


class TestWiring:
    """The helper is inert unless it is called in the right place. These pin
    the seam the behavioural tests cannot see."""

    def test_main_extracts_before_parse_args(self, leerie):
        """ORDER IS THE WHOLE BUG. After `parse_args` the run-id has already
        been swallowed by `task` and the damage is done."""
        src = inspect.getsource(leerie.main)
        assert "_extract_resume_run_id(" in src, "helper never called"
        assert (src.index("_extract_resume_run_id(")
                < src.index("args = ap.parse_args(argv)")), (
            "extraction must happen BEFORE parse_args")

    def test_main_assigns_it_to_run_id(self, leerie):
        """Extracting without assigning would leave `run_id` None and still
        auto-pick — a silent no-op fix."""
        src = inspect.getsource(leerie.main)
        assert "args.run_id = positional_run_id" in src

    def test_extraction_is_scoped_to_resume(self, leerie):
        """ANTI-VACUITY: `list` has its own positionals (`list status paused`,
        `list chains`). Taking argv[0] there would break them."""
        src = inspect.getsource(leerie.main)
        i = src.index("_extract_resume_run_id(")
        window = src[max(0, i - 200):i + 120]
        assert "is_resume" in window, (
            "extraction must be guarded on is_resume, not applied to `list`")

    def test_resolve_run_id_consumes_args_run_id(self, leerie):
        """Closes the loop: the value we assign is the one actually used."""
        src = inspect.getsource(leerie.main)
        assert "resolve_run_id(leerie_root, args.run_id" in src


class TestConflictingIds:
    """Positional AND --run-id together is ambiguous. Silently preferring one
    is how a user ends up resuming a run they did not name."""

    def test_conflicting_ids_are_rejected(self, leerie):
        src = inspect.getsource(leerie.main)
        i = src.index("args.run_id = positional_run_id")
        window = src[max(0, i - 400):i]
        assert "die(" in window and "conflict" in window.lower(), (
            "a positional that disagrees with --run-id must die(), not be "
            "silently overridden")

    def test_matching_ids_are_not_an_error(self, leerie):
        """Passing the same id both ways is redundant, not wrong."""
        src = inspect.getsource(leerie.main)
        assert "args.run_id != positional_run_id" in src, (
            "the guard must compare values, not merely detect presence")


class TestTheHazardThatMadeThisSevere:
    def test_task_is_only_read_on_the_NON_resume_branch(self, leerie):
        """Why binding the positional to `run_id` is safe — proved
        structurally, not by a substring.

        `_run_phases` branches on `if args.resume:`; every `args.task` read
        must live in that statement's `else`. If one appeared on the resume
        side, taking the positional away from `task` would starve it, and
        `resume` would start failing with "a task description is required".

        An earlier version of this test asserted `"args.task" not in
        getsource(main)` — which passes trivially, because the reads are in
        `_run_phases`, not `main`. It proved nothing about the claim in its
        own docstring. This walks the AST instead."""
        tree = ast.parse(inspect.getsource(leerie._run_phases))
        resume_ifs = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.If)
            and isinstance(n.test, ast.Attribute)
            and n.test.attr == "resume"]
        assert resume_ifs, "`if args.resume:` not found — structure changed"

        def _task_reads(nodes):
            return [n for node in nodes for n in ast.walk(node)
                    if isinstance(n, ast.Attribute) and n.attr == "task"
                    and isinstance(n.value, ast.Name) and n.value.id == "args"]

        on_resume = [n for br in resume_ifs for n in _task_reads(br.body)]
        on_fresh = [n for br in resume_ifs for n in _task_reads(br.orelse)]
        assert not on_resume, (
            "args.task is read on the RESUME branch — taking the positional "
            "run-id away from `task` would starve it")
        assert on_fresh, (
            "no args.task read on the fresh-run branch: the structure moved, "
            "so this test is no longer proving anything")

    def test_resume_is_the_only_verb_through_argparse(self, leerie):
        """`stop`/`kill`/`accept-blocked` handle their positional inside the
        launcher and exit; only `resume` and `list` round-trip here."""
        src = inspect.getsource(leerie.main)
        assert 'argv[0] == "resume"' in src
        assert 'argv[0] == "list"' in src
        for verb in ("stop", "kill", "accept-blocked"):
            assert f'argv[0] == "{verb}"' not in src


def test_launcher_comment_no_longer_claims_no_rewrite_needed():
    """The launcher forwarded the positional under a comment asserting the
    orchestrator 'understands the bare verb form natively — No rewrite
    needed.' That was false and is what stopped anyone looking further.

    It is true NOW (the orchestrator handles it), but the sentence that
    misled must not survive unqualified."""
    import pathlib
    launcher = (pathlib.Path(__file__).resolve().parent.parent
                / "leerie").read_text()
    assert "No rewrite needed." not in launcher or (
        "_extract_resume_run_id" in launcher), (
        "the launcher still asserts no rewrite is needed without pointing at "
        "the orchestrator-side handling that makes it true")
