"""The input rule of DESIGN §6 *Leerie's runs of the repository's commands
are confined too* (review round 20, execution H1): every path leerie hands
a confined command lies inside its grant. Real code throughout: the real
shim, `_measurement_grants`, `_redrive_ast_*`. An AST script is a confined
command started synchronously, so it runs only under the execution lock its
caller holds (`_ts_script_run`; DESIGN §6 *A held-out evaluation runs
alone*)."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _git, _grants, _probe, pytestmark, run)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _top_level(module: ast.Module) -> dict[str, ast.AST]:
    """The module's functions and its classes' methods, by name: a nested
    helper (a local `_invoke`, a closure) never stands in for one."""
    out: dict[str, ast.AST] = {}
    for n in module.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[n.name] = n
        elif isinstance(n, ast.ClassDef):
            for m in n.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.setdefault(m.name, m)
    return out


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _held(leerie, fn, *args, **kw):
    """`fn(*args, **kw)` under the execution lock, held shared, as every
    caller of a synchronous AST script holds it."""
    async def go():
        async with leerie._exec_lock().shared():
            return fn(*args, **kw)
    return asyncio.run(go())


# --- execution H1: every input inside the grant ------------------------------

TS_COPY = (Path(pwd.getpwuid(os.getuid()).pw_dir)
           / ".cache/leerie-lanes/r20e/ts/node_modules/typescript")
BASE = ("export function f(x: any) {\n  if (x == null) { throw new "
        "Error('n'); }\n  return x;\n}\n")
HEAD = ("export function f(x: any) {\n  try { return x.y; } catch (e) "
        "{ return 0; }\n}\n")


def _ts_bundle() -> Path | None:
    if TS_COPY.is_dir():
        return TS_COPY
    return None


@pytest.fixture()
def ts_tree(leerie, run, monkeypatch):
    ts = _ts_bundle()
    if ts is None or shutil.which("node") is None:
        pytest.skip("no node or no typescript copy on this host")
    monkeypatch.setattr(leerie, "BUNDLED_TYPESCRIPT", ts)
    tree = run["wts"]["staging"]
    (tree / "src").mkdir()
    for body, msg in ((BASE, "b"), (HEAD, "h")):
        (tree / "src" / "lib.ts").write_text(body)
        _git(tree, "add", "-A")
        _git(tree, "-c", "user.email=t@x", "-c", "user.name=t", "commit",
             "-qm", msg)
    r = subprocess.run(["git", "-C", str(tree), "rev-list", "-2", "HEAD"],
                       capture_output=True, text=True, check=True)
    head, base = r.stdout.split()
    return tree, base, head


def test_the_ast_gate_runs_once_leerie_uses_the_run_tmp(
        leerie, run, ts_tree, monkeypatch):
    """With leerie's temp files in `<run>/tmp/` (what `_run_phases` does
    first), the gate still reads its base copy: it is in the grant's own
    temp directory. Before, the script died reading it and gate (d+)
    voided every escalated round."""
    tree, base, head = ts_tree
    st = _st(run)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setenv("TMPDIR", os.environ.get("TMPDIR", "/tmp"))
    leerie._use_run_tmp(st)
    g = leerie._measurement_grants(st, tree)
    hits = _held(leerie, leerie._redrive_ast_hits, tree, base, head,
                 grants=g)
    assert hits is not None
    eg = _held(leerie, leerie._redrive_escalation_gate, tree, base, head,
               grants=g)
    assert eg["ast"] is not None


def test_an_input_outside_the_grant_stops_the_run(leerie, run):
    """`_ts_script_run` and `_run_streaming` refuse an argv path the grant
    does not reach — a file in leerie's own `<run>/tmp/` — rather than let
    the command fail as "could not measure"."""
    st = _st(run)
    view = _view(run)
    g = leerie._measurement_grants(st, view)
    own_tmp = run["run_dir"] / "tmp"
    own_tmp.mkdir(exist_ok=True)
    f = own_tmp / "base.ts"
    f.write_text("x\n")
    assert leerie._argv_outside_grant(["node", str(f)], g) == str(f)
    inside = view / "in.ts"
    inside.write_text("x\n")
    assert leerie._argv_outside_grant(
        ["node", str(inside), sys.executable], g) is None
    with pytest.raises(SystemExit):
        _held(leerie, leerie._ts_script_run, ["cat", str(f)], tree=view,
              grants=g, timeout=30)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_streaming(
            ["cat", str(f)], grants=g, cwd=str(view), timeout=30,
            verbosity="quiet"))


def test_an_ast_script_without_the_execution_lock_stops_the_run(leerie,
                                                                 run):
    """`_ts_script_run` cannot wait for the lock (it is synchronous), so a
    call that does not hold it — one that could run beside a held-out
    evaluation — stops the run before anything is spawned; under the lock
    the same call runs."""
    view = _view(run)
    g = leerie._measurement_grants(_st(run), view)
    inside = view / "in.txt"
    inside.write_text("IN-GRANT\n")
    with pytest.raises(SystemExit):
        leerie._ts_script_run(["cat", str(inside)], tree=view, grants=g,
                              timeout=30, text=True)
    r = _held(leerie, leerie._ts_script_run, ["cat", str(inside)],
              tree=view, grants=g, timeout=30, text=True)
    assert r.returncode == 0 and r.stdout == "IN-GRANT\n", r


def test_every_ast_script_caller_holds_the_execution_lock():
    """By rule over the module: every call that reaches `_ts_script_run`
    from a coroutine sits inside `async with _exec_lock().shared()`. The
    synchronous helpers between (`_ts_resolvable`, `_redrive_ast_*`) are
    followed to the coroutine that calls them."""
    tree = ast.parse(LEERIE_PY.read_text())
    funcs = _top_level(tree)

    def callees(fn) -> set[str]:
        return {getattr(c.func, "id", None) or getattr(c.func, "attr", "")
                for c in ast.walk(fn) if isinstance(c, ast.Call)} | {
            a.id for c in ast.walk(fn) if isinstance(c, ast.Call)
            for a in c.args if isinstance(a, ast.Name)}
    reach = {"_ts_script_run"}
    changed = True
    while changed:
        changed = False
        for name, fn in funcs.items():
            if (isinstance(fn, ast.FunctionDef) and name not in reach
                    and callees(fn) & reach):
                reach.add(name)
                changed = True
    sync_reach = reach - {"_ts_script_run"}
    assert {"_ts_resolvable", "_redrive_ast_hits",
            "_redrive_ast_available", "_redrive_escalation_gate"} <= reach
    bad = []
    for name, fn in funcs.items():
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        held = {id(c) for w in ast.walk(fn) if isinstance(w, ast.AsyncWith)
                and any("_exec_lock" in ast.unparse(i.context_expr)
                        for i in w.items)
                for c in ast.walk(w)}
        for c in ast.walk(fn):
            if not isinstance(c, ast.Call):
                continue
            names = {getattr(c.func, "id", None)} | {
                a.id for a in c.args if isinstance(a, ast.Name)} | {
                a.func.id for a in c.args if isinstance(a, ast.Call)
                and isinstance(a.func, ast.Name)} | {
                x.id for a in c.args if isinstance(a, ast.Call)
                for x in a.args if isinstance(x, ast.Name)}
            if names & reach and id(c) not in held:
                bad.append((name, c.lineno))
    assert sync_reach and bad == [], bad
