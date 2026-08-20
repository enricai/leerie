"""Source-coupling guard for refactor-004: `_settle_subtask`'s
`incomplete-handoff` and `needs-clarification` branches must both call one
shared helper for checkpoint validation + continuation-cap enforcement,
rather than each inlining the identical block (orchestrator/leerie.py, near
:29803-29850 pre-refactor).
"""
from __future__ import annotations

import ast
import inspect

import orchestrator.leerie as leerie


def _settle_subtask_source() -> str:
    return inspect.getsource(leerie._settle_subtask)


def test_shared_helper_is_defined_inside_settle_subtask():
    src = _settle_subtask_source()
    tree = ast.parse(src)
    func = tree.body[0]
    assert isinstance(func, ast.AsyncFunctionDef)
    nested_names = {
        n.name for n in ast.walk(func)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_checkpoint_and_continuation_gate" in nested_names


def test_both_branches_call_the_shared_helper():
    src = _settle_subtask_source()
    # Isolate each `if status == "..."` branch body by locating its guard
    # line and taking the text up to the next `if status ==` (or `continue`
    # if the branch is short) so this asserts against the real per-branch
    # calls, not just aggregate presence anywhere in the function.
    handoff_idx = src.index('if status == "incomplete-handoff"')
    clarify_idx = src.index('if status == "needs-clarification"')
    failed_idx = src.index('if status == "failed"')

    handoff_body = src[handoff_idx:clarify_idx]
    clarify_body = src[clarify_idx:failed_idx]

    assert handoff_body.count("_checkpoint_and_continuation_gate(") == 1
    assert clarify_body.count("_checkpoint_and_continuation_gate(") == 1

    # Neither branch should still inline the checkpoint-validation call
    # directly -- that's the duplication this refactor removes.
    assert "_validate_checkpoint(" not in handoff_body
    assert "_validate_checkpoint(" not in clarify_body


def test_clarification_side_trip_is_untouched_and_branch_specific():
    src = _settle_subtask_source()
    clarify_idx = src.index('if status == "needs-clarification"')
    failed_idx = src.index('if status == "failed"')
    clarify_body = src[clarify_idx:failed_idx]

    handoff_idx = src.index('if status == "incomplete-handoff"')
    handoff_body = src[handoff_idx:clarify_idx]

    # The clarification-specific side trip (_surface_clarification + spec
    # rewrite) must remain solely in the needs-clarification branch.
    assert "_surface_clarification(" in clarify_body
    assert "_surface_clarification(" not in handoff_body
    assert '_clarification_answers' in clarify_body
    assert '_clarification_answers' not in handoff_body


def test_helper_signature_returns_dict_or_none():
    tree = ast.parse(_settle_subtask_source())
    func = tree.body[0]
    helper = next(
        n for n in ast.walk(func)
        if isinstance(n, ast.FunctionDef)
        and n.name == "_checkpoint_and_continuation_gate"
    )
    # Synchronous helper (no awaits needed -- _validate_checkpoint is sync),
    # taking the worker result dict and returning a blocked dict or None.
    assert not isinstance(helper, ast.AsyncFunctionDef)
    assert len(helper.args.args) == 1
