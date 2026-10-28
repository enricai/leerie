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
