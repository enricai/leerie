"""Every orchestrator-side run of the repo's build, lint or test command goes
through one forbidden-command gate, `_blt_shell_argv` (DESIGN §9 *A command
the task forbids*, "Every orchestrator-side run … passes one gate").

Structural: no shell launch outside the helper, and no caller hands it an
empty forbidden list. Substance: on a real pytest repo whose conftest leaves
a marker when pytest starts, each runner refuses a forbidden command without
starting it, and the integration gate never runs the raw test command."""
from __future__ import annotations

import ast
import asyncio
import subprocess
from pathlib import Path

import pytest

PYTEST = "python3 -m pytest -q -p no:cacheprovider"
SRC = Path(__file__).resolve().parents[1] / "orchestrator" / "leerie.py"
SHELLS = {"bash", "sh", "/bin/bash", "/bin/sh", "dash", "zsh"}


def _tree() -> ast.Module:
    return ast.parse(SRC.read_text())


def _enclosing(tree: ast.Module) -> dict[int, str]:
    """id(node) → name of the innermost top-level-or-nested function."""
    owner: dict[int, str] = {}

    def walk(node: ast.AST, fn: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (child.name if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn)
            owner[id(child)] = name
            walk(child, name)
    walk(tree, "<module>")
    return owner


def _is_shell_list(node: ast.AST) -> bool:
    if not isinstance(node, (ast.List, ast.Tuple)) or len(node.elts) < 2:
        return False
    a, b = node.elts[0], node.elts[1]
    return (isinstance(a, ast.Constant) and a.value in SHELLS
            and isinstance(b, ast.Constant) and isinstance(b.value, str)
            and b.value.startswith("-") and "c" in b.value[1:])


def test_no_shell_launch_outside_the_gate():
    tree = _tree()
    owner = _enclosing(tree)
    bad = []
    for node in ast.walk(tree):
        if _is_shell_list(node) and owner[id(node)] != "_blt_shell_argv":
            bad.append((node.lineno, owner[id(node)]))
        if isinstance(node, ast.keyword) and node.arg == "shell" and (
                not isinstance(node.value, ast.Constant)
                or node.value.value is not False):
            bad.append((node.value.lineno, "shell=True"))
        if isinstance(node, ast.Attribute) and node.attr in (
                "create_subprocess_shell", "system", "popen"):
            bad.append((node.lineno, node.attr))
    assert bad == [], (
        "a shell launch outside _blt_shell_argv skips the forbidden-command "
        f"check (DESIGN §9): {bad}")


# Every way leerie.py starts a process. The shell-list test above sees only
# `[sh, -c, …]`; a repo command exec'd as an argv list (`_ci_run`,
# `_cov_contained`, the recipe runner, settle-by-execution's verify argvs)
# went past it on every run (review r2 H1).
_SUBPROCESS = {"run", "Popen", "check_output", "check_call", "call",
               "getoutput", "getstatusoutput"}
_LAUNCH_FUNCS = {"run_proc", "_run_streaming", "_run_contained"}

# A literal argv led by one of these runs a program leerie itself chose —
# never a command read from the repository, a CI file, the plan or the
# provision recipe.
LEERIE_OWNED_HEADS = {
    "git": "repository plumbing",
    "claude": "the worker CLI",
    "mise": "toolchain install from the repo's pinned versions",
    "ps": "process-tree inspection",
    "grep": "a fixed search",
    "node": "leerie's own scripts (TEST_CASE_FACTS, REDRIVE_AST_GATE) "
            "and a require.resolve probe",
    "bash": "leerie's scripts/ and the operator's .leerie-setup.sh — "
            "never `-c`, which the shell-list test refuses",
    "tar": "the re-drive self-check's copy of a working tree: fixed "
           "flags, its file list NUL-separated on stdin (`--null`, so a "
           "name is never read as an option)",
}

# Launches whose argv is computed but is not a repository command. Each
# entry is the enclosing function and why it is exempt. A new launch of a
# computed argv must go through `_blt_shell_argv` or be added here with a
# reason a reviewer can check.
EXEMPT = {
    "run_proc": "the launch primitive; its callers are checked",
    "_run_streaming": "the launch primitive; its callers are checked",
    "_run_contained": "the contained-launch primitive; callers are checked",
    "_invoke": "spawns the `claude -p` worker argv `claude_p` builds",
    "_worktree_add_detached": "a `git worktree add` argv",
    "_out": "`_snapshot_repo_state`'s git helper (literal git args)",
    "_network_reach": "leerie's own probe script under sys.executable",
    "_repo_mentions": "a `git grep` argv",
    "_start_test_db_cluster": "postgres binaries for leerie's test database",
    "_stop_test_db_cluster": "postgres binaries for leerie's test database",
    "_drop_test_db": "postgres binaries for leerie's test database",
    "_sleep_then_reexec": "re-execs the orchestrator itself",
    "_git_z_output": "git commands with `-z` path output, read as bytes",
}


def _launch_argv(node: ast.Call) -> tuple[str, ast.AST] | None:
    """`(launcher, argv expression)` when `node` starts a process."""
    f = node.func
    name = None
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
        mod, attr = f.value.id, f.attr
        if mod == "subprocess" and attr in _SUBPROCESS:
            name = f"subprocess.{attr}"
        elif mod == "asyncio" and attr.startswith("create_subprocess"):
            name = f"asyncio.{attr}"
        elif mod == "os" and (attr.startswith(("exec", "spawn", "posix_spawn"))
                              or attr in ("system", "popen")):
            name = f"os.{attr}"
    elif isinstance(f, ast.Name) and f.id in _LAUNCH_FUNCS:
        name = f.id
    if name is None:
        return None
    if not node.args:
        kw = next((k.value for k in node.keywords
                   if k.arg in ("args", "argv", "cmd")), None)
        return (name, kw) if kw is not None else None
    a = node.args[0]
    if isinstance(a, ast.Starred):
        a = a.value
    elif (name.startswith("asyncio.") and isinstance(a, ast.Constant)):
        # create_subprocess_exec("git", ...): a literal program word.
        return name, ast.List(elts=[a], ctx=ast.Load())
    return name, a


def _gated_names(fn: ast.AST) -> dict[str, int]:
    """Names `fn` binds from `_blt_shell_argv(...)` → earliest line."""
    out: dict[str, int] = {}
    for n in ast.walk(fn):
        if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name)
                and n.value.func.id == "_blt_shell_argv"):
            for t in n.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = min(out.get(t.id, n.lineno), n.lineno)
    return out


def _repo_command_launches_outside_the_gate(tree: ast.Module
                                            ) -> list[tuple[int, str, str]]:
    owner = _enclosing(tree)
    fns = {id(n): n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    by_name: dict[str, list[ast.AST]] = {}
    for n in fns.values():
        by_name.setdefault(n.name, []).append(n)
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        got = _launch_argv(node)
        if got is None:
            continue
        launcher, argv = got
        fn = owner.get(id(node), "<module>")
        if (isinstance(argv, (ast.List, ast.Tuple)) and argv.elts
                and isinstance(argv.elts[0], ast.Constant)
                and argv.elts[0].value in LEERIE_OWNED_HEADS):
            continue
        if isinstance(argv, ast.Name) and any(
                _gated_names(f).get(argv.id, 10 ** 9) < node.lineno
                for f in by_name.get(fn, [])):
            continue
        if fn in EXEMPT:
            continue
        bad.append((node.lineno, fn, f"{launcher}({ast.unparse(argv)[:60]})"))
    return bad


def test_no_repo_command_launch_outside_the_gate():
    """Every process leerie starts either has a literal argv led by a
    program leerie chose, or an argv bound from `_blt_shell_argv` in the
    same function, or sits in an EXEMPT launcher that runs no repository
    command. `_ci_run`, `_cov_contained`, `_ensure_worktree_deps` and
    settle-by-execution's `_once` exec'd repo commands as argv lists, past
    the forbidden-command check."""
    bad = _repo_command_launches_outside_the_gate(_tree())
    assert bad == [], (
        "a launch of a computed argv outside _blt_shell_argv can run a "
        f"command the task forbids (DESIGN §9): {bad}")


def test_the_exempt_list_names_live_launchers():
    """The converse: an EXEMPT entry whose function no longer launches
    anything is a stale hole a new launcher could hide behind."""
    tree = _tree()
    owner = _enclosing(tree)
    launching = {owner.get(id(n)) for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and _launch_argv(n) is not None}
    assert sorted(set(EXEMPT) - launching) == []


def test_the_guard_flags_an_argv_launch_of_a_repo_command():
    """The guard itself, on a synthetic module: a computed argv is flagged
    whatever its form, a gated or literal one is not."""
    src = (
        "async def ci(argv, rec):\n"
        "    await _run_contained([str(a) for a in argv], cwd='.')\n"
        "async def deps(e):\n"
        "    await _run_streaming(e['command'], cwd='.')\n"
        "def raw(cmd):\n"
        "    subprocess.run(cmd)\n"
        "async def ok(cmd, forb):\n"
        "    argv = _blt_shell_argv(cmd, forb)\n"
        "    await _run_contained(argv, cwd='.')\n"
        "async def fixed():\n"
        "    await run_proc(['git', 'status'])\n")
    bad = _repo_command_launches_outside_the_gate(ast.parse(src))
    assert sorted(b[1] for b in bad) == ["ci", "deps", "raw"]


def _calls(tree: ast.Module, name: str) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == name]


def _empty(node: ast.AST | None) -> bool:
    return node is None or (isinstance(node, (ast.List, ast.Tuple))
                            and not node.elts) or (
        isinstance(node, ast.Constant) and not node.value)


def test_every_gate_call_carries_the_task_forbidden_list():
    tree = _tree()
    gate = _calls(tree, "_blt_shell_argv")
    assert len(gate) >= 4      # _measure_blt, capture, acceptance, probe
    for c in gate:
        forb = c.args[1] if len(c.args) > 1 else next(
            (k.value for k in c.keywords if k.arg == "forbidden"), None)
        assert not _empty(forb), f"line {c.lineno}: empty forbidden list"
    for c in _calls(tree, "_measure_blt"):
        forb = next((k.value for k in c.keywords if k.arg == "forbidden"),
                    None)
        assert not _empty(forb), (
            f"line {c.lineno}: _measure_blt needs the task's forbidden list")


def test_the_gate_refuses_a_hit_and_builds_a_plain_shell(leerie):
    with pytest.raises(leerie.ForbiddenByTask) as e:
        leerie._blt_shell_argv("pnpm run test", ["pnpm test"])
    assert e.value.hit == "pnpm test"
    with pytest.raises(leerie.ForbiddenByTask):
        leerie._blt_shell_argv(f"{PYTEST} --junitxml=/r.xml",
                               [{"command": PYTEST,
                                 "scope": "whole_suite"}],
                               source=PYTEST)
    assert leerie._blt_shell_argv("pnpm run lint", ["pnpm test"]) == [
        "bash", "-c", "pnpm run lint"]


# --- substance: a real pytest repo ------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "py"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@x")
    _git(repo, "config", "user.name", "t")
    (repo / "calc.py").write_text("def one():\n    return 1\n")
    mark = tmp_path / "pytest-started"
    (repo / "conftest.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent))\n"
        f"pathlib.Path({str(mark)!r}).write_text('x')\n")
    (repo / "test_calc.py").write_text(
        "from calc import one\n\n\ndef test_one():\n    assert one() == 1\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "config.toml").write_text(
        f'test = "{PYTEST}"\n'
        f'test_scoped = "{PYTEST} {{test_files}}"\n')
    (repo / ".gitignore").write_text(".leerie/runs/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _mark(repo: Path) -> Path:
    return repo.parent / "pytest-started"


def _st(leerie, repo: Path, forbidden: list | None = None):
    st = leerie.State(repo.parent / "state", "run-gate", repo_root=repo)
    st.data = {"verbosity": "quiet", "worker_count": 0}
    if forbidden is not None:
        st.data["process_constraints"] = _pc(forbidden)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    return st


ANY = [PYTEST]
# Scope-typed entries live under `entries` (`_forbidden_specs`).
WHOLE = [{"command": "python3 -m pytest", "scope": "whole_suite",
          "test_roots": []}]


def _pc(forbidden: list) -> dict:
    if all(isinstance(f, str) for f in forbidden):
        return {"forbidden_commands": forbidden}
    return {"forbidden_commands": [f["command"] for f in forbidden],
            "entries": forbidden}


def _capture(leerie, st, repo, cmd, files=None):
    return asyncio.run(leerie._capture_failing_test_ids(
        str(repo), cmd, st, dict(leerie.DEFAULT_CAPS), label="gate",
        log_path=st.run_dir / "logs" / "gate.log", timeout=120,
        serial=True, files=files))


def test_capture_never_starts_a_forbidden_command(leerie, repo):
    st = _st(leerie, repo, ANY)
    assert _capture(leerie, st, repo, PYTEST) is None
    assert _capture(leerie, st, repo, PYTEST, ["test_calc.py"]) is None
    assert not _mark(repo).exists()
    # A whole-suite forbid still lets the scoped run through ...
    st.data["process_constraints"] = _pc(WHOLE)
    got = _capture(leerie, st, repo, PYTEST, ["test_calc.py"])
    assert got is not None and got["ids"] == [] and _mark(repo).exists()
    # ... and refuses the whole suite, read before the serial flags
    # (`-p no:xdist`), whose value a whole-suite match takes for a test
    # operand.
    _mark(repo).unlink()
    assert _capture(leerie, st, repo, "python3 -m pytest -q") is None
    assert not _mark(repo).exists()


def test_acceptance_file_and_parse_probe_never_start_one(leerie, repo):
    st = _st(leerie, repo, ANY)
    caps = dict(leerie.DEFAULT_CAPS)
    log = st.run_dir / "logs" / "acc.log"
    assert asyncio.run(leerie._run_acceptance_file(
        st, caps, str(repo), "test_calc.py", log, "acc")) is None
    ok, why = asyncio.run(leerie._acceptance_parse_probe(
        st, caps, str(repo), "test_calc.py", ["calc.py"], log, "acc"))
    assert ok is None and "forbids" in why
    assert not _mark(repo).exists()
    st.data.pop("process_constraints")
    assert asyncio.run(leerie._run_acceptance_file(
        st, caps, str(repo), "test_calc.py", log, "acc")) is True
    assert _mark(repo).exists()


def _integration(leerie, repo, monkeypatch, forbidden):
    p1 = _git(repo, "rev-parse", "HEAD")
    (repo / "calc.py").write_text("def one():\n    return 1 * 1\n")
    _git(repo, "commit", "-qam", "repair")
    st = _st(leerie, repo, forbidden)
    ran: list[tuple[str, list | None]] = []
    real = leerie._capture_failing_test_ids

    async def spy(tree, cmd, *a, **kw):
        ran.append((cmd, kw.get("files")))
        return await real(tree, cmd, *a, **kw)

    async def no_tree(*a, **kw):
        return None
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", spy)
    monkeypatch.setattr(leerie, "_integration_tree", no_tree)
    out = asyncio.run(leerie._integration_test_failures(
        st, "feat-001", repo, p1, p1, p1, dict(leerie.DEFAULT_CAPS),
        log_path=st.run_dir / "logs" / "integ.log", parent_cache={}))
    return out, ran


def test_integration_gate_never_runs_the_raw_test_command(leerie, repo,
                                                         monkeypatch):
    """The gate read `resolve_blt(...)["test"]` and handed it to the
    capture, which ran it raw whenever the files' scoped form did not
    render. It now passes only the scoped command."""
    out, ran = _integration(leerie, repo, monkeypatch, WHOLE)
    assert out["scope"] == ["test_calc.py"] and out["skipped"] is None
    assert ran == [(f"{PYTEST} test_calc.py", ["test_calc.py"])]
    assert _mark(repo).exists()


def test_integration_gate_skips_a_forbidden_scoped_command(leerie, repo,
                                                          monkeypatch):
    out, ran = _integration(leerie, repo, monkeypatch, ANY)
    assert ran == [] and not _mark(repo).exists()
    assert "forbids" in out["skipped"]


# --- argv launches (review r2 H1) -------------------------------------------

def test_the_gate_takes_an_exec_argv_and_checks_it_as_the_shell_would(leerie):
    with pytest.raises(leerie.ForbiddenByTask) as e:
        leerie._blt_shell_argv(["python3", "-m", "pytest", "-q"], WHOLE)
    assert e.value.hit == "python3 -m pytest"
    # An argv is returned as exec'd — never wrapped in a shell.
    assert leerie._blt_shell_argv(["python3", "-m", "pytest", "t/a.py"],
                                  WHOLE) == ["python3", "-m", "pytest",
                                             "t/a.py"]
    assert leerie._blt_shell_argv(("pnpm", "run", "lint"),
                                  ["pnpm test"]) == ["pnpm", "run", "lint"]


def test_ci_parity_never_starts_a_forbidden_check(leerie, repo):
    """Reviewer's driver: the task forbids the whole suite and the CI check
    is `python3 -m pytest -q`. `_ci_run` started it (rc 0)."""
    st = _st(leerie, repo, WHOLE)
    out = asyncio.run(leerie._ci_run(
        ["python3", "-m", "pytest", "-q"], tree=repo, rec={}, st=st,
        caps=dict(leerie.DEFAULT_CAPS), label="final-tests"))
    assert not _mark(repo).exists(), "CI parity ran the forbidden suite"
    assert out["skipped"] == "forbidden_by_task"
    assert out["rc"] is None and out["passed"] is False


def test_ci_select_unselects_a_forbidden_check(leerie, repo):
    checks = [{"id": "lint", "kind": "lint",
               "argv": ["python3", "-m", "pytest", "-q"],
               "trigger": ["pull_request"]},
              {"id": "strict", "kind": "lint", "argv": ["ruff", "check"],
               "strict_argv": ["python3", "-m", "pytest"],
               "trigger": ["pull_request"]}]
    recs = {r["id"]: r for r in leerie._ci_select(checks, repo, WHOLE)}
    assert recs["lint"]["selected"] is False
    assert recs["lint"]["reason"] == "forbidden_by_task"
    assert recs["lint"]["forbidden"] == "python3 -m pytest"
    assert recs["strict"]["strict_argv"] is None
    assert recs["strict"]["strict_forbidden"] == "python3 -m pytest"
    # No forbidden list: the same check is judged on its own merits.
    plain = {r["id"]: r for r in leerie._ci_select(checks, repo)}
    assert plain["lint"].get("reason") != "forbidden_by_task"
    assert plain["strict"]["strict_argv"] == ["python3", "-m", "pytest"]


def test_coverage_runs_never_start_a_forbidden_command(leerie, repo):
    st = _st(leerie, repo, WHOLE)
    out = asyncio.run(leerie._cov_contained(
        ["python3", "-m", "pytest", "-q"], tree=repo, st=st,
        caps=dict(leerie.DEFAULT_CAPS), sid="feat-001", tag="t"))
    assert not _mark(repo).exists()
    assert out["rc"] is None and out["skipped"] == "forbidden_by_task"
    st.data.pop("process_constraints")
    out = asyncio.run(leerie._cov_contained(
        ["python3", "-m", "pytest", "-q"], tree=repo, st=st,
        caps=dict(leerie.DEFAULT_CAPS), sid="feat-001", tag="t"))
    assert out["rc"] == 0 and _mark(repo).exists()


def _recipe_st(leerie, repo, forbidden):
    st = _st(leerie, repo, forbidden)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": ["python3", "-m", "pytest", "-q"]},
        {"kind": "build", "command": ["python3", "-m", "pytest"]}]}
    return st


@pytest.mark.parametrize("kind", ["install", "build"])
def test_recipe_entries_never_start_a_forbidden_command(leerie, repo, kind):
    st = _recipe_st(leerie, repo, WHOLE)
    st.data["provision"]["recipe"] = [
        e for e in st.data["provision"]["recipe"] if e["kind"] == kind]
    leerie._DEPS_INSTALLED.discard(str(repo.resolve()))
    try:
        ok = asyncio.run(leerie._ensure_worktree_deps(
            str(repo), st, dict(leerie.DEFAULT_CAPS),
            log_path=st.run_dir / "logs" / "deps.log", verbosity="quiet"))
    finally:
        leerie._DEPS_INSTALLED.discard(str(repo.resolve()))
    assert not _mark(repo).exists()
    # A refused install is a failed install; a refused build is skipped.
    assert ok is (kind == "build")


def test_settle_launch_refuses_what_the_upfront_check_missed(leerie, repo,
                                                             monkeypatch):
    """The plan strip and `_forbidden_verify_argv` refuse a forbidden
    verify argv before it runs; with both bypassed the launch itself still
    refuses it, and the verdict is none, never fail."""
    st = _st(leerie, repo, WHOLE)
    monkeypatch.setattr(leerie, "_forbidden_verify_argv", lambda *a: [])
    monkeypatch.setattr(leerie, "_ensure_worktree_deps",
                        lambda *a, **k: asyncio.sleep(0, True))
    sub = {"id": "feat-001",
           "verify": [{"argv": ["python3", "-m", "pytest", "-q"]}]}
    rec = asyncio.run(leerie._settle_by_execution(
        sub, str(repo), st, dict(leerie.DEFAULT_CAPS), sid="feat-001"))
    assert not _mark(repo).exists()
    assert rec["verdict"] == "none" and "forbids" in rec["why"]
