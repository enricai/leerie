"""Every path variable a confined CLI is given lies inside its grant (DESIGN
§6 *Every confined CLI is given its own directories, by one builder*).

Review round 22 exec H2: every fresh run without `--skip-smoke` died in
preflight. `_use_run_tmp` points the orchestrator's `TMPDIR` at
`<run-dir>/tmp/`; the smoke test's `claude -p` inherited it, its grant names
no such directory, and the CLI's first act — `mkdir $TMPDIR/claude-<uid>`,
whatever its flags — failed with EROFS. `claude_p` made each worker's own
directories itself and the smoke test made none: two builders, one of them
missing.

Two kinds of test. The rule: every confined spawn site (whatever calls
`_worker_grants` or `_contained_claude_argv`, and the Fly integrator's guard
helper) goes through `_confined_cli_env`, and `_worker_dirs` has no other
caller. The behaviour: each spawn's real CLI environment — captured by a
stub `claude` run confined through the real `_invoke` and the Landlock shim,
or printed by `worker-guard.py` — names no path in the state root outside
its grant and every `_WORKER_ENV_DIRS` variable inside what it may write; the
stub also does what the CLI does at start, and the spawn succeeds.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
GUARD_PY = REPO_ROOT / "scripts" / "worker-guard.py"
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _abi() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lk", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.abi()


needs_landlock = pytest.mark.skipif(
    _abi() < 3, reason="the host kernel has no Landlock ABI 3")


# --- the rule --------------------------------------------------------------

def _callers(path: Path, name: str) -> set[str]:
    """The outermost function (or `<module>`) of every call to `name`."""
    tree = ast.parse(path.read_text())
    out: set[str] = set()

    def walk(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            own = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and owner == "<module>":
                own = child.name
            if isinstance(child, ast.Call):
                f = child.func
                called = (f.id if isinstance(f, ast.Name) else
                          f.attr if isinstance(f, ast.Attribute) else None)
                if called == name:
                    out.add(own)
            walk(child, own)

    walk(tree, "<module>")
    return out


def _sources() -> list[Path]:
    return [LEERIE_PY, *sorted((REPO_ROOT / "scripts").rglob("*.py"))]


def test_every_confined_cli_spawn_takes_its_environment_from_the_builder():
    """A spawn site is whatever derives a CLI's grant (`_worker_grants`) or
    builds its argv (`_contained_claude_argv`); each must also call the one
    builder, so none can hand a CLI an environment built another way."""
    missing, sites = [], set()
    for src in _sources():
        spawners = (_callers(src, "_worker_grants")
                    | _callers(src, "_contained_claude_argv"))
        builders = _callers(src, "_confined_cli_env")
        for fn in spawners:
            sites.add((src.name, fn))
            if fn not in builders:
                missing.append(f"{src.name}:{fn}")
    # The scan sees the three known spawns, so an empty result is not a
    # blind scan.
    assert {("leerie.py", "claude_p"), ("leerie.py", "preflight"),
            ("worker-guard.py", "main")} <= sites, sites
    assert missing == [], (
        f"confined CLI spawn(s) not built through _confined_cli_env: "
        f"{missing}")


def test_only_the_builder_makes_a_cli_s_own_directories():
    callers = {(src.name, fn) for src in _sources()
               for fn in _callers(src, "_worker_dirs")}
    assert callers == {("leerie.py", "_confined_cli_env")}, callers


def test_the_builder_names_every_worker_env_dir(leerie, tmp_path,
                                                monkeypatch):
    """Every `_WORKER_ENV_DIRS` variable, not only the ones a caller used to
    set: `CLAUDE_CODE_TMPDIR` left inherited would name the orchestrator's."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    env = leerie._confined_cli_env(run_dir, "w1")
    own = run_dir / "workers" / "w1"
    assert env == {var: str(own / ("config" if var == "CLAUDE_CONFIG_DIR"
                                   else "tmp"))
                   for var in leerie._WORKER_ENV_DIRS}
    assert (own / "config").is_dir() and (own / "tmp").is_dir()


# --- the behaviour ---------------------------------------------------------

def _paths(value: str) -> list[str]:
    return [p for p in value.split(os.pathsep) if os.path.isabs(p)]


def _beneath(p: str, roots: list[str]) -> bool:
    rp = Path(os.path.realpath(p))
    return any(rp.is_relative_to(os.path.realpath(r)) for r in roots)


def _violations(leerie, env: dict, rw: list[str], ro: list[str],
                state_root: Path) -> list[str]:
    """Every variable naming a path in the state root its grant does not
    cover, and every `_WORKER_ENV_DIRS` variable outside what it writes. The
    rule is the grant's own: outside the state root a worker reads
    everything (`_reads_outside`), inside it only what it is granted."""
    bad = []
    for k, v in env.items():
        for p in _paths(v):
            if (_beneath(p, [str(state_root)])
                    and not _beneath(p, rw + ro)):
                bad.append(f"{k}={p}")
    for k in leerie._WORKER_ENV_DIRS:
        if k in env and not _beneath(env[k], rw):
            bad.append(f"{k}={env[k]} (not writable)")
    return bad


STUB = r'''#!/usr/bin/env python3
import json, os, sys
sys.stdin.read()
# The CLI's first act, whatever its flags (measured: EROFS before settings).
d = os.path.join(os.environ.get("TMPDIR") or "/tmp", f"claude-{os.getuid()}")
try:
    os.makedirs(d, exist_ok=True)
    open(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".probe"), "w").close()
except OSError as e:
    print(f"Error processing settings: {e}", file=sys.stderr)
    sys.exit(1)
with open(os.environ["STUB_LOG"], "w") as f:
    json.dump(dict(os.environ), f)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "ok", "session_id": "s1",
                  "structured_output": {"title": "t", "body": "b",
                                        "used_template": None},
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''


@pytest.fixture
def world(leerie, tmp_path, monkeypatch):
    """A run directory, the orchestrator's temp directory moved into it as
    `_run_phases` does before preflight (`_use_run_tmp`), a stub `claude`
    and a spy on the grant every spawn derives."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(STUB)
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    st = leerie.State(tmp_path / "state", "run-env", repo_root=repo)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    st.data = {"verbosity": "quiet", "worker_count": 0}
    st.save()
    # `_use_run_tmp` writes the process's environment and `tempfile`;
    # restored by monkeypatch.
    monkeypatch.setenv("TMPDIR", os.environ.get("TMPDIR", "/tmp"))
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setattr(leerie, "_SYSTEM_TEMP_DIR", {})
    run_tmp = leerie._use_run_tmp(st)
    # A variable the old builders never named, inherited from the
    # orchestrator: it must not reach a CLI.
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(run_tmp))
    # The stub's log goes where every worker of the run may write.
    log = st.run_dir / "workers" / "cache" / "stub-env.json"
    log.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STUB_LOG", str(log))
    grants: list = []
    real = leerie._worker_grants

    def spy(**kw):
        g = real(**kw)
        grants.append(g)
        return g

    monkeypatch.setattr(leerie, "_worker_grants", spy)
    return st, log, grants, run_tmp


def _assert_inside(leerie, st, log, grants):
    env = json.loads(log.read_text())
    assert grants, "no grant was derived"
    g = grants[-1]
    state_root = Path(st.run_dir).parent.parent
    assert _violations(leerie, env, list(g.rw), list(g.ro),
                       state_root) == []
    return env


@needs_landlock
def test_the_smoke_test_runs_after_the_run_tmp_is_set(leerie, world,
                                                      monkeypatch):
    """The finding's shape, end to end: `_use_run_tmp`, then preflight's
    smoke test through the real `_invoke` and shim. Before the fix the stub
    exits 1 at its `mkdir` and preflight dies."""
    st, log, grants, run_tmp = world
    monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
    monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
    monkeypatch.setattr(leerie, "_disk_free_ratio", lambda _p: 0.9)
    asyncio.run(leerie.preflight(st.run_dir, skip_smoke=False,
                                 model="haiku", verbosity="quiet"))
    env = _assert_inside(leerie, st, log, grants)
    own = st.run_dir / "workers" / "smoke"
    assert env["TMPDIR"] == str(own / "tmp") != str(run_tmp)
    assert env["CLAUDE_CONFIG_DIR"] == str(own / "config")
    # Its temp directory goes with the call, as a worker's does.
    assert not (own / "tmp").exists()


@needs_landlock
def test_a_worker_spawned_by_claude_p_too(leerie, world):
    st, log, grants, run_tmp = world
    asyncio.run(leerie.claude_p(
        user_prompt="hello", system_prompt="sys", schema_key="pr_writer",
        cwd=str(st.repo_root), allowed_tools="Read", max_turns=1,
        autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
        model="haiku", sid="w-env"))
    env = _assert_inside(leerie, st, log, grants)
    assert env["CLAUDE_CODE_TMPDIR"] == env["TMPDIR"] != str(run_tmp)


@needs_landlock
def test_the_fly_integrators_environment_too(leerie, world, tmp_path):
    """`worker-guard.py` prints the integrator's environment and its shim
    argv; the same rule over what it printed."""
    st, _log, _grants, run_tmp = world
    p = subprocess.run(
        [sys.executable, "-I", str(GUARD_PY), leerie.ACT_TOOLS,
         str(st.repo_root), str(st.run_dir / "state.json"), "integrator-x"],
        capture_output=True, text=True, timeout=120, env=os.environ)
    assert p.returncode == 0, p.stderr
    got = subprocess.run(
        ["bash", "-c", p.stdout + '\nprintf "%s\\0" "${guard_env[@]}"; '
         'printf "\\1"; printf "%s\\0" "${guard_confine[@]}"'],
        capture_output=True, text=True, timeout=60, check=True).stdout
    env_part, confine_part = got.split("\1", 1)
    env = {**os.environ, **dict(w.split("=", 1)
                                for w in env_part.split("\0") if w)}
    words = [w for w in confine_part.split("\0") if w]
    rw = [words[i + 1] for i, w in enumerate(words) if w == "--rw"]
    ro = [words[i + 1] for i, w in enumerate(words) if w == "--ro"]
    assert _violations(leerie, env, rw, ro,
                       Path(st.run_dir).parent.parent) == []
    assert env["TMPDIR"] != str(run_tmp)


@needs_landlock
def test_the_fly_guard_refuses_to_confine_without_a_worker_id(leerie,
                                                              tmp_path):
    """Without a `<wid>` there are no own directories to name, so there is
    no confined integrator: exit 1, which the payload reads as skip."""
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text("{}")
    p = subprocess.run(
        [sys.executable, "-I", str(GUARD_PY), leerie.ACT_TOOLS,
         str(tmp_path), str(run_dir / "state.json")],
        capture_output=True, text=True, timeout=120)
    assert p.returncode == 1
    assert "guard_confine" not in p.stdout
    assert "worker id" in p.stderr
