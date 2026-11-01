"""DESIGN §6 *A command in a worker's tree is kept from `/tmp` as the worker
is* (review round 20, isolation H2), rule 1 of *A measurement may write
`/tmp`; a held-out evaluation has it alone* (`/tmp` read only under the
lock), and the input rule of *Leerie's runs of the repository's commands
are confined too* (round 20, execution H1: every path leerie hands a
confined command lies inside its grant).

Real code throughout: `_run_acceptance_file` (its `_held_out_section`), the
real shim, `_measurement_grants`, `_worker_grants`, `_redrive_ast_*`; only
`_acceptance_cmd` is stubbed, as tests/test_measurement_tmp.py does."""
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
from tests.test_measurement_tmp import shared  # noqa: F401  (fixture)
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _git, _grants, _probe, pytestmark, run)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _stream(leerie, st, tree: Path, script: str) -> tuple[int, str]:
    g = leerie._measurement_grants(st, tree)
    return asyncio.run(leerie._run_streaming(
        ["bash", "-c", script], grants=g, cwd=str(tree), timeout=60,
        verbosity="quiet", tail_lines=50))


# --- H2: no relay of /tmp into a worker's tree --------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_the_ast_gate_cannot_relay_tmp_into_the_implementers_tree(
        leerie, run, shared, monkeypatch):
    """The round-20 proof, turned round: a held-out leftover in `/tmp` and
    the implementer's own `typescript` that copies it into staging — the
    re-drive's AST gate, run exactly as the re-drive runs it, now reads
    nothing of `/tmp`, so nothing reaches the tree."""
    st = _st(run)
    view = _view(run)
    staging = run["wts"]["wt-a"]            # a committing tree (I6)
    secret = f"HELD_OUT_ASSERTION_{uuid.uuid4().hex[:12]}"
    (shared / "cache").write_text("")
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda _st, rels, _tree: f"bash {rels[0]}")
    held = view / "held.sh"
    held.write_text(f"# {secret}\ncat \"$0\" >> {shared}/cache\nexit 1\n")
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.sh",
        run["run_dir"] / "logs" / "acc.log", "acc"))
    held.unlink()
    assert v is False
    # The documented residual: still there.
    assert secret in (shared / "cache").read_text()

    ts = staging / "node_modules" / "typescript"
    ts.mkdir(parents=True)
    (ts / "package.json").write_text(json.dumps({"name": "typescript",
                                                 "main": "index.js"}))
    leak = staging / "leak.txt"
    (ts / "index.js").write_text(
        "const fs = require('fs');\n"
        f"let got = 'TMP-DENIED';\n"
        f"try {{ got = fs.readFileSync({json.dumps(str(shared / 'cache'))},"
        " 'utf8'); } catch (e) {}\n"
        f"fs.writeFileSync({json.dumps(str(leak))}, got);\n"
        "module.exports = { createSourceFile() {} };\n")
    g = leerie._measurement_grants(st, staging)
    assert g.shared_tmp == ()
    leerie._redrive_ast_available(staging, grants=g)
    # The planted code ran (it wrote its tree) and read nothing of /tmp.
    assert leak.read_text() == "TMP-DENIED"
    assert secret not in leak.read_text()


def test_a_command_in_a_worker_tree_neither_reads_nor_writes_tmp(
        leerie, run, shared):
    """Through `_run_streaming`, under the lock: a view keeps `/tmp`; every
    committing tree — staging and a subtask tree (tree repositories), a
    declared clone — gets none, as its worker gets none."""
    st = _st(run)
    (shared / "marker").write_text("m\n")
    script = (f"cat {shared}/marker >/dev/null && echo READ; "
              f"echo x > {shared}/planted-$$ && echo WROTE; "
              "echo x > \"$TMPDIR/own\" && echo OWN; true")
    rc, tail = _stream(leerie, st, _view(run), script)
    assert rc == 0 and "READ" in tail and "WROTE" in tail, tail
    clone = run["run_dir"] / "worktrees" / "declared-clone"
    clone.mkdir()
    leerie._declare_worker_tree(clone)
    for tree in (run["wts"]["staging"], run["wts"]["wt-a"], clone):
        assert leerie._measurement_grants(st, tree).shared_tmp == (), tree
        rc, tail = _stream(leerie, st, tree, script)
        assert rc == 0, tail
        assert "READ" not in tail and "WROTE" not in tail, (tree, tail)
        assert "OWN" in tail, (tree, tail)


def test_every_committing_workers_tree_is_a_worker_tree(leerie, run):
    """`_worker_grants` declares the cwd of every committing worker, so a
    command leerie runs there later gets no `/tmp` either."""
    st = _st(run)
    keys = sorted(leerie._COMMITTING_WORKERS)
    assert {"implementer", "acceptance_writer", "surrogate_fixer",
            "falsifier"} <= set(keys)
    for key in keys:
        clone = run["run_dir"] / "worktrees" / f"clone-{key}"
        clone.mkdir()
        subprocess.run(["git", "init", "-q", str(clone)], check=True)
        assert not leerie._is_worker_tree(run["run_dir"], clone, None)
        _grants(leerie, run, schema_key=key, cwd=clone)
        assert leerie._measurement_grants(st, clone).shared_tmp == (), key
    # A non-committing worker's checkout is not declared.
    other = run["run_dir"] / "worktrees" / "judge-checkout"
    other.mkdir()
    _grants(leerie, run, schema_key="delivery_judge", cwd=other,
            tools=leerie.ACT_TOOLS)
    assert not leerie._is_worker_tree(run["run_dir"], other, None)


def test_a_worker_tree_measured_with_tmp_first_stops_the_run(leerie, run):
    """The other side of the declaration: a tree a grant already gave
    `/tmp` cannot become a committing worker's — what was read there may be
    in it."""
    st = _st(run)
    clone = run["run_dir"] / "worktrees" / "undeclared-clone"
    clone.mkdir()
    assert leerie._measurement_grants(st, clone).shared_tmp != ()
    with pytest.raises(SystemExit):
        leerie._declare_worker_tree(clone)
    with pytest.raises(SystemExit):
        _grants(leerie, run, schema_key="falsifier", cwd=clone)


def _calls_in(func: str, callee: str) -> list[ast.Call]:
    tree = ast.parse(LEERIE_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == func:
            return [c for c in ast.walk(node) if isinstance(c, ast.Call)
                    and getattr(c.func, "id", None) == callee]
    raise AssertionError(f"no {func}")


@pytest.mark.parametrize("func", ["_write_acceptance_set",
                                  "_acceptance_surrogate", "_falsify_items"])
def test_every_committing_clone_is_declared_before_it_is_installed(func):
    """The clones made for a committing worker are declared before the
    first command leerie runs in them, their install."""
    decl = _calls_in(func, "_declare_worker_tree")
    assert decl
    for d in decl:
        tree = ast.unparse(d.args[0])
        inst = [c for c in _calls_in(func, "_ensure_worktree_deps")
                if ast.unparse(c.args[0]) in (tree, f"str({tree})")]
        assert inst, tree
        assert all(d.lineno < c.lineno for c in inst), tree


# --- rule 1: /tmp only under the lock ----------------------------------------

def test_a_command_started_outside_run_streaming_reads_no_tmp(
        leerie, run, shared):
    """An AST script or `mise ls` (`_confined_argv` without `shared_tmp`)
    in a view: neither reads nor writes `/tmp`, its private temp directory
    still its own. Under `_run_streaming`'s lock both are granted."""
    g = leerie._measurement_grants(_st(run), _view(run))
    (shared / "marker").write_text("m\n")
    got = _probe(leerie, g, f"""
private tmp|echo x > "$TMPDIR/ok"
read tmp|cat {shared}/marker
write tmp|echo x > {shared}/planted
""", dict(g.env))
    assert got == {"private tmp": True, "read tmp": False,
                   "write tmp": False}
    assert not any(Path(os.path.realpath(p)).is_relative_to(shared)
                   for p in g.ro)


def _within_tmp_lock(tree: ast.Module) -> list[int]:
    """Lines of every `_confined_argv(..., shared_tmp=True)` not inside an
    `async with _tmp_lock()...` block."""
    held: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith) and any(
                "_tmp_lock" in ast.unparse(i.context_expr)
                for i in node.items):
            held |= {c.lineno for c in ast.walk(node)
                     if isinstance(c, ast.Call)}
    bad = []
    for c in ast.walk(tree):
        if (isinstance(c, ast.Call)
                and getattr(c.func, "id", None) == "_confined_argv"
                and any(k.arg == "shared_tmp"
                        and not (isinstance(k.value, ast.Constant)
                                 and k.value.value is False)
                        for k in c.keywords)
                and c.lineno not in held):
            bad.append(c.lineno)
    return bad


def test_every_tmp_granting_spawn_holds_the_tmp_lock():
    """By rule over the module: a `_confined_argv` call that grants `/tmp`
    (the only way any grant reaches it) sits under the run's `/tmp` lock,
    and every grant's reads leave `/tmp` out."""
    tree = ast.parse(LEERIE_PY.read_text())
    assert _within_tmp_lock(tree) == []
    granting = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
                and getattr(c.func, "id", None) == "_confined_argv"
                and any(k.arg == "shared_tmp" for k in c.keywords)]
    assert granting, "the rule found no `/tmp`-granting spawn"
    for c in ast.walk(tree):
        if isinstance(c, ast.Call) and \
                getattr(c.func, "id", None) == "_reads_outside":
            assert any(k.arg == "tmp" and isinstance(k.value, ast.Constant)
                       and k.value.value is False for k in c.keywords), \
                c.lineno


def test_the_lock_rule_catches_a_spawn_outside_the_lock():
    bad = ast.parse(
        "def f(g):\n    return _confined_argv(g, ['x'], shared_tmp=True)\n"
        "async def h(g):\n    async with _tmp_lock().shared():\n"
        "        return _confined_argv(g, ['x'], shared_tmp=True)\n")
    assert _within_tmp_lock(bad) == [2]


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
    hits = leerie._redrive_ast_hits(tree, base, head, grants=g)
    assert hits is not None
    eg = leerie._redrive_escalation_gate(tree, base, head, grants=g)
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
        leerie._ts_script_run(["cat", str(f)], tree=view, grants=g,
                              timeout=30)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_streaming(
            ["cat", str(f)], grants=g, cwd=str(view), timeout=30,
            verbosity="quiet"))
