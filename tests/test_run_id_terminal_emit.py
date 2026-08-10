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


def test_run_id_log_call_has_no_enclosing_gate():
    """The `log(f"run id: ...")` call must sit at `_run_phases`' body
    depth, with NO enclosing `if` of any kind.

    The predecessor of this test asserted only that the call was not
    inside `if not args.resume:`. That is the wrong invariant, and it
    passed for months while the bug was live: the call had been lifted out
    of that specific gate but left nested inside `if "waves" not in
    st.data:` -> `if "plans_after_classify" not in st.data:`, so every
    resume that had already checkpointed past classify — every resume into
    execution, the common case — still announced nothing. Naming one
    forbidden gate can never catch the next one; "no gate at all" is the
    property that actually holds."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = (repo_root / "orchestrator" / "leerie.py").read_text()
    tree = ast.parse(src)

    parent = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    sites = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "log"
                and node.args):
            continue
        if "run id" not in ast.unparse(node.args[0]):
            continue
        gates, cur = [], parent.get(node)
        while cur is not None:
            if isinstance(cur, ast.If):
                gates.append(ast.unparse(cur.test))
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sites.append((cur.name, gates))
                break
            cur = parent.get(cur)

    assert sites, "no `log(f\"run id: ...\")` call found at all"
    for fn_name, gates in sites:
        assert not gates, (
            f"the run-id log() in {fn_name}() is gated behind {gates} — it "
            f"must fire on every path, fresh and resume alike"
        )


def test_run_id_log_call_still_exists_unconditionally():
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = (repo_root / "orchestrator" / "leerie.py").read_text()
    assert 'log(f"run id: {st.run_id}")' in src


def test_resume_into_execution_announces_the_run_id(tmp_path, capsys,
                                                    monkeypatch):
    """Behavioural counterpart to the structural check above, and the one
    that actually reproduces the reported failure: a state.json already
    checkpointed past `plans_after_classify` must still print the run id.

    This is the state the old placement was silent on, so it fails before
    the hoist and passes after — a falsification that changes an
    observable rather than a source-text shape."""
    import asyncio
    import importlib.util
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "leerie_emit", repo_root / "orchestrator" / "leerie.py")
    leerie = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(leerie)

    run_dir = tmp_path / "runs" / "abc123def456"
    run_dir.mkdir(parents=True)

    class _St:
        run_id = "abc123def456"
        run_dir = None
        path = None
        data = {}

        def load(self):
            return True

        def save(self):
            pass

    st = _St()
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    # Checkpointed past classify and all the way through scheduling — the
    # shape a resume into execution actually has.
    st.data = {
        "task": "do a thing",
        "worker_count": 12,
        "categories": ["feature-implementation"],
        "waves": [["feat-001"]],
        "plans_after_classify": [],
        "finished_at": None,
    }

    class _Args:
        resume = True

    # Stop immediately after the announcement; everything downstream needs
    # a real run and is covered elsewhere.
    class _Stop(BaseException):
        pass

    def _boom(*_a, **_k):
        raise _Stop

    monkeypatch.setattr(leerie, "_validate_resume_state", _boom)

    with pytest.raises(_Stop):
        asyncio.run(leerie._run_phases(
            _Args(), {}, tmp_path, st, "codebase", "normal", {}, {}))

    out = capsys.readouterr()
    assert "run id: abc123def456" in (out.out + out.err), (
        "a resume that already checkpointed past classify announced no run "
        "id — the N8 failure"
    )
