"""N8: the run id must be announced on every terminal exit path, not just
the single `log()` call that used to sit inside `if not args.resume`.

`die()` runs at module scope with no `st` in hand at most call sites, so
the only channel available to it is the module-level `_CURRENT_RUN_ID`,
set once by `State.__init__` via `_set_current_run_id`. These tests pin:

  1. `die()` includes the run id in its message once a `State` has been
     constructed (mirroring how a real run reaches most `die()` call
     sites — after `State(...)` has already run).
  2. `die()` falls back to its plain, unchanged message when no `State`
     has been constructed yet (e.g. an early preflight `die()`).
  3. `State.__init__` is the sole caller of `_set_current_run_id` — the
     wiring the fix depends on.
  4. The `log(f"run id: ...")` call in the resumable-planning entry point
     is unconditional (not gated behind `if not args.resume`), fixing the
     "resume never announces its id at all" gap the investigation notes
     for this subtask on `orchestrator/leerie.py:27480` describe.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest


@pytest.fixture(autouse=True)
def _reset_current_run_id(leerie):
    # _CURRENT_RUN_ID is module-level and the `leerie` fixture is
    # session-scoped, so a run id set by one test would otherwise leak
    # into every later test's die() output.
    leerie._set_current_run_id(None)
    yield
    leerie._set_current_run_id(None)


def test_die_includes_run_id_once_state_constructed(leerie, tmp_path, capsys):
    leerie_root = tmp_path / "leerie-root"
    leerie.State(leerie_root, "run-abc123")

    with pytest.raises(SystemExit):
        leerie.die("something went wrong")

    err = capsys.readouterr().err
    assert "run-abc123" in err, f"die() output missing run id: {err!r}"
    assert "something went wrong" in err


def test_die_plain_message_when_no_run_id_set(leerie, capsys):
    with pytest.raises(SystemExit):
        leerie.die("no state constructed yet")

    err = capsys.readouterr().err
    assert err.strip() == "leerie: error: no state constructed yet"


def test_die_uses_the_most_recently_constructed_state(leerie, tmp_path, capsys):
    # A second State() (e.g. accept-blocked's target_st, or a resumed run
    # reloading a different run_id) must move the pointer, not append to it.
    leerie_root = tmp_path / "leerie-root"
    leerie.State(leerie_root, "first-run")
    leerie.State(leerie_root, "second-run")

    with pytest.raises(SystemExit):
        leerie.die("boom")

    err = capsys.readouterr().err
    assert "second-run" in err
    assert "first-run" not in err


def test_state_init_calls_the_run_id_setter(leerie):
    src = textwrap.dedent(inspect.getsource(leerie.State.__init__))
    assert "_set_current_run_id(run_id)" in src, (
        "State.__init__ must call _set_current_run_id(run_id) so die() "
        "(and any other module-scope caller) can announce the run id on "
        "every terminal exit path"
    )


def test_run_id_log_call_is_not_gated_behind_resume():
    """The `log(f\"run id: ...\")` call in the resumable-planning entry
    point must sit OUTSIDE `if not args.resume:` — a resumed run
    previously never announced its id at all."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = (repo_root / "orchestrator" / "leerie.py").read_text()
    tree = ast.parse(src)

    found = []

    class Visitor(ast.NodeVisitor):
        def visit_If(self, node: ast.If) -> None:
            test_src = ast.unparse(node.test)
            if test_src == "not args.resume":
                for stmt in ast.walk(node):
                    if (
                        isinstance(stmt, ast.Call)
                        and isinstance(stmt.func, ast.Name)
                        and stmt.func.id == "log"
                    ):
                        call_src = ast.unparse(stmt)
                        if "run id" in call_src:
                            found.append(call_src)
            self.generic_visit(node)

    Visitor().visit(tree)
    assert not found, (
        "the run-id log() call must not sit inside `if not args.resume:` "
        f"— found: {found}"
    )


def test_run_id_log_call_still_exists_unconditionally():
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = (repo_root / "orchestrator" / "leerie.py").read_text()
    assert 'log(f"run id: {st.run_id}")' in src
