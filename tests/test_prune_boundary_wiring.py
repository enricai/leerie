"""The N31 prune must be scoped to INTEGRATED subtasks, never the whole wave.

`tests/test_prune_subtask_worktree.py` covers the helper in isolation —
idempotence, branch survival, sid scoping. Nothing covered the *call site*,
and the call site is where the load-bearing decision lives.

Measured 2026-08-12: changing `for sid in integrated:` to `for sid in wave:`
in `phase_execute` left **76 prune/worktree tests green**. That mutation
prunes the worktrees of `blocked` and `failed` subtasks — exactly the trees
an operator inspects by hand, and exactly what the N21/N22/N23 recovery
verbs (`accept-blocked`, `accept-integration`) exist to let them act on.
The work order names this boundary as load-bearing:

    never prune `blocked` / `incomplete-handoff` — those are exactly the
    ones an operator inspects by hand

Two independent pins, because either alone is weak:

1. **Structural** — an AST walk asserting the `_prune_subtask_worktree`
   call's enclosing `for` iterates `integrated`. A source substring check is
   not equivalent: the region is dense with comments naming both `wave` and
   `integrated` while explaining the distinction, so a text scan matches the
   prose describing what it forbids (the trap `tests/test_subreaper.py` and
   `tests/test_production_evidence.py` both document).
2. **Behavioural** — `integrate_wave` must never report a non-complete
   subtask as integrated, which is what makes `integrated` a safe iterable
   in the first place. If that filter regressed, pin 1 would still pass
   while blocked worktrees got pruned anyway.
"""
from __future__ import annotations

import ast
import inspect
import textwrap


def _phase_execute_tree(leerie) -> ast.AST:
    return ast.parse(textwrap.dedent(inspect.getsource(leerie.phase_execute)))


def _prune_loops(tree: ast.AST) -> list[ast.For]:
    """The INNERMOST `for` loop(s) enclosing a `_prune_subtask_worktree` call.

    Innermost matters: `phase_execute`'s whole body sits inside
    `for wi in range(start, len(waves))`, so a naive "any For containing the
    call" walk returns that outer wave loop as well and the iterable
    assertion below fails against correct code.
    """
    candidates: list[ast.For] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        if any(isinstance(inner, ast.Call)
               and isinstance(inner.func, ast.Name)
               and inner.func.id == "_prune_subtask_worktree"
               for inner in ast.walk(node)):
            candidates.append(node)
    # Drop any candidate that merely encloses another candidate.
    return [c for c in candidates
            if not any(other is not c and other in set(ast.walk(c))
                       for other in candidates)]


def test_prune_is_called_from_phase_execute_at_all(leerie):
    """Anti-vacuity: every assertion below is hollow if the call is gone.

    A prune that is never invoked is not a safe prune, it is the N31 defect
    (worktrees surviving until run end) reintroduced silently.
    """
    loops = _prune_loops(_phase_execute_tree(leerie))
    assert len(loops) == 1, (
        f"expected exactly one _prune_subtask_worktree loop in phase_execute, "
        f"found {len(loops)}")


def test_prune_loop_iterates_integrated_not_the_whole_wave(leerie):
    """THE boundary. `wave` contains blocked/failed sids; `integrated` does not."""
    loop = _prune_loops(_phase_execute_tree(leerie))[0]
    assert isinstance(loop.iter, ast.Name), (
        "the prune loop's iterable is no longer a plain name — re-read it and "
        "confirm blocked/failed subtasks still cannot reach the prune")
    assert loop.iter.id == "integrated", (
        f"the prune loop iterates {loop.iter.id!r}, not 'integrated'. "
        "Blocked and failed subtasks keep their worktrees so an operator can "
        "inspect them and settle them with accept-blocked / accept-integration; "
        "pruning the whole wave destroys exactly those trees.")


def test_structural_check_is_not_a_source_substring_scan(leerie):
    """Guard-the-guard: prove the surrounding source really does mention the
    forbidden iterable, so a future rewrite of these tests as a text scan is
    known to be unsound rather than merely discouraged."""
    src = inspect.getsource(leerie.phase_execute)
    assert "for sid in integrated:" in src
    # `wave` appears throughout the function (the loop variable, log lines,
    # the comment explaining why blocked sids keep their worktrees), so
    # "does the source mention wave" can never distinguish the two forms.
    assert "wave" in src


def test_integrate_wave_never_reports_a_non_complete_subtask(leerie):
    """Behavioural half: `integrated` is only safe because integrate_wave
    filters on status == "complete". Pinned directly so a regression there
    cannot quietly widen what the prune loop consumes."""
    src = inspect.getsource(leerie.integrate_wave)
    tree = ast.parse(textwrap.dedent(src))

    # The filter is a guard clause: `if results[sid].get("status") != "complete": continue`
    compares = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Compare)
        and any(isinstance(c, ast.Constant) and c.value == "complete"
                for c in n.comparators)
    ]
    assert compares, (
        "integrate_wave no longer compares a subtask's status against "
        "'complete' — the prune loop's input may now include blocked or "
        "failed subtasks")

    # Polarity matters, and a bare "some Compare exists" check misses it: the
    # guard clause is `if status != "complete": continue`, so flipping the
    # operator inverts which subtasks reach `integrated` while leaving the
    # comparison node in place.
    #
    # An earlier attempt at this asserted `operators <= {"NotEq", "Eq"}` and
    # `"NotEq" in operators or "Eq" in operators` — both of which a flip
    # satisfies, so it was still vacuous for the property it named. What
    # actually discriminates is the pairing of the operator with what the
    # branch DOES: `!=` must skip (continue), `==` must not.
    skipping = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (isinstance(test, ast.Compare)
                and any(isinstance(c, ast.Constant) and c.value == "complete"
                        for c in test.comparators)):
            continue
        op = type(test.ops[0]).__name__
        body_skips = any(isinstance(stmt, ast.Continue) for stmt in node.body)
        skipping.append((op, body_skips))

    assert skipping, (
        "no `if <status> <op> 'complete':` branch found in integrate_wave — "
        "the filter that makes `integrated` safe for the prune is gone")
    for op, body_skips in skipping:
        if op == "NotEq":
            assert body_skips, (
                "`!= \"complete\"` no longer skips the subtask; non-complete "
                "subtasks would now reach `integrated` and be pruned")
        elif op == "Eq":
            assert not body_skips, (
                "`== \"complete\"` now skips — the polarity is inverted, so "
                "COMPLETE subtasks are dropped and blocked ones integrated")


def test_prune_helper_never_deletes_the_branch(leerie):
    """The prune is safe only because the branch outlives the worktree —
    integrate_wave merged the BRANCH, and finalize/PR history still needs it.
    `_reset_subtask_worktree` deliberately does delete it; these must not
    converge."""
    prune_src = inspect.getsource(leerie._prune_subtask_worktree)
    assert "-D" not in prune_src, (
        "_prune_subtask_worktree appears to delete a branch (`-D`); the "
        "branch must outlive the worktree — integrate_wave merged the "
        "BRANCH, and finalize/PR history still needs it")
    reset_src = inspect.getsource(leerie._reset_subtask_worktree)
    assert "-D" in reset_src, (
        "_reset_subtask_worktree no longer deletes the branch — if that moved, "
        "re-check that the prune/reset distinction this test relies on still holds")
