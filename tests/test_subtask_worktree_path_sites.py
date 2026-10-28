"""Every subtask worktree path comes from `_subtask_worktree_path`.

Since subtask worktrees became run-id-prefixed (`worktrees/<run-id[:12]>-
<sid>`, `_worktree_dirname`), a path joined by hand as `worktrees / <sid>`
names a directory no real run has. The coverage re-drive did exactly that:
it found "no worktree" for every subtask, settled each one `unmeasured`, and
so never re-drove anything on a real run, while its tests — whose fixture
was built at the same hand-joined path — stayed green.

The rule, checked on the AST: a `... / "worktrees" / X` join may name a
string literal, an f-string, a `_worktree_dirname(...)` call, or the
parameter `name` of the helper-worktree builders (`ci-base`,
`acceptance-eval`, ... — literal-derived, never a subtask id). Anything
else is a hand-built subtask path and must go through the helper."""
from __future__ import annotations

import ast

from tests.conftest import LEERIE_PY

# The helper-worktree builders' parameter: always a literal-derived name.
_ALLOWED_NAMES = {"name"}


def _offending_joins(source: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        left = node.left
        if not (isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div)
                and isinstance(left.right, ast.Constant)
                and left.right.value == "worktrees"):
            continue
        right = node.right
        if isinstance(right, (ast.Constant, ast.JoinedStr)):
            continue
        if (isinstance(right, ast.Call) and isinstance(right.func, ast.Name)
                and right.func.id == "_worktree_dirname"):
            continue
        if isinstance(right, ast.Name) and right.id in _ALLOWED_NAMES:
            continue
        out.append((node.lineno, ast.unparse(node)))
    return out


def test_no_hand_built_subtask_worktree_path():
    bad = _offending_joins(LEERIE_PY.read_text())
    assert bad == [], (
        "join `worktrees / <sid>` by hand names the pre-prefix layout; use "
        f"_subtask_worktree_path(leerie_dir, sid): {bad}")


def test_the_rule_flags_the_shape_it_exists_for():
    # The coverage re-drive's original line, and the variable spellings a
    # subtask id goes by in this file.
    for line in ('t = Path(leerie_dir) / "worktrees" / sid',
                 't = leerie_dir / "worktrees" / s',
                 't = str(Path(d) / "worktrees" / subtask_id)'):
        assert _offending_joins(line), line
    for line in ('t = st.run_dir / "worktrees" / name',
                 't = d / "worktrees" / "staging"',
                 't = d / "worktrees" / f"integ-{sid}-{name}"',
                 't = d / "worktrees" / _worktree_dirname(d.name, sid)'):
        assert not _offending_joins(line), line


def test_helper_matches_the_script_layout(leerie, tmp_path):
    run_dir = tmp_path / "runs" / "20261028-abcdef-cov"
    got = leerie._subtask_worktree_path(run_dir, "feat-001")
    assert got == run_dir / "worktrees" / "20261028-abc-feat-001"
