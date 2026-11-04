"""No copy of the credentials outlives its run's process, and every deleter
of a run's directories deletes them (DESIGN §6 *No copy of the credentials
outlives its run's process*, *Every deleter of a run's directories deletes
them*; review round 18 M1).

The run's CLI configuration template (`<run-dir>/claude-config/`) is
read-only and holds the operator's `.credentials.json`; `workers/` holds
writable copies. Real `_claude_config_template`, `_worker_dirs`, the real
`main()` in its own process ended every way it can end, a real finalize,
the preflight reap and the real launcher `prune --apply`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_prune_verb import LAUNCHER, OLD, _repo

SECRET = "OPERATOR-SECRET"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".claude").mkdir(parents=True)
    (h / ".claude" / ".credentials.json").write_text(
        json.dumps({"claudeAiOauth": {"accessToken": SECRET}}))
    (h / ".claude" / "settings.json").write_text("{}")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


def _a_run_with_credentials(leerie, run: Path) -> None:
    """The run's first worker makes the template and its own config."""
    run.mkdir(parents=True, exist_ok=True)
    tpl = leerie._claude_config_template(run)
    assert SECRET in (tpl / ".credentials.json").read_text()
    assert not os.access(tpl, os.W_OK)
    config, _tmp = leerie._worker_dirs(run, "impl-s1", None)
    assert SECRET in (config / ".credentials.json").read_text()


def _no_secret_under(path: Path) -> bool:
    for d, _dirs, files in os.walk(path):
        for f in files:
            try:
                if SECRET in Path(d, f).read_text(errors="ignore"):
                    return False
            except OSError:
                pass
    return True


def test_the_owner_aware_removal_removes_read_only_and_unreadable_dirs(
        leerie, tmp_path):
    top = tmp_path / "t"
    (top / "ro" / "deep").mkdir(parents=True)
    (top / "ro" / "deep" / "f").write_text("x")
    (top / "noread").mkdir()
    (top / "noread" / "g").write_text("y")
    os.chmod(top / "ro" / "deep", 0o500)
    os.chmod(top / "ro", 0o500)
    os.chmod(top / "noread", 0o000)
    (top / "link").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").mkdir()
    assert leerie._rmtree_owned(top) is True
    assert not os.path.lexists(top)
    assert (tmp_path / "outside").is_dir()       # a link is never followed
    assert leerie._rmtree_owned(top) is True     # already gone


def test_the_owner_aware_removal_reports_a_survivor(leerie, tmp_path):
    parent = tmp_path / "locked"
    (parent / "child").mkdir(parents=True)
    os.chmod(parent, 0o500)                      # the child cannot go
    try:
        if os.geteuid() == 0:
            pytest.skip("root removes it anyway")
        assert leerie._rmtree_owned(parent / "child") is False
        assert (parent / "child").is_dir()
    finally:
        os.chmod(parent, 0o700)


# Drives the real `main()` in its own process, `_orchestrate` replaced by a
# stand-in that makes every per-process copy — a worker's configuration (so
# the template), the measurement directories, the trees' stores, the store
# fetch's staging — and then leaves `main()` the way `kind` says. The
# process's exit is what must remove them (DESIGN §6 *No copy of the
# credentials outlives its run's process*), so the check is made after the
# process is gone.
DRIVER = r"""
import asyncio, atexit, importlib.util, json, os, subprocess, sys
from pathlib import Path
root, work, kind = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
spec = importlib.util.spec_from_file_location(
    "leerie", root / "orchestrator" / "leerie.py")
m = importlib.util.module_from_spec(spec)
sys.modules["leerie"] = m
spec.loader.exec_module(m)
os.chdir(work / "repo")
report = {}


def note():
    (work / "report.json").write_text(json.dumps(report))


def copies(st):
    # Named here, not read from leerie: the list under test.
    if kind != "scratch_only":
        m._worker_dirs(st.run_dir, "s1", None)
    for name in ("measure", "tree-stores", "stores"):
        (st.run_dir / name / "x").mkdir(parents=True, exist_ok=True)


async def finalize(st, caps):
    st.data.update(waves=[["feat-001"]], completed_waves=1, worker_count=2,
                   subtask_status={"feat-001": "complete"},
                   working_branch="main", base_sha="0" * 40)
    st.save()

    async def script(name, *a, **k):
        return subprocess.CompletedProcess(["bash", name], 0, "", "")

    async def proc(cmd, **k):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    async def capture(*a, **k):
        # The dependency capture's worker: `claude_p` makes its directories.
        m._worker_dirs(st.run_dir, "dep-capture", None)

    async def compose(*a, **k):
        return None
    m._run_script, m.run_proc = script, proc
    m.capture_repo_deps, m._compose_pr_via_llm = capture, compose
    await m.phase_finalize(st.leerie_root, st, no_push=True,
                           no_verify=False, caps=caps, models={}, efforts={})
    report["template_after_finalize"] = (
        st.run_dir / m.CLAUDE_CONFIG_TEMPLATE).is_dir()


def at_exec(*a):
    report["template_at_exec"] = (
        Path(report["run_dir"]) / m.CLAUDE_CONFIG_TEMPLATE).exists()
    note()
    os._exit(0)                    # an exec: the image ends, no exit handlers


RAISE = {
    "worker_error": lambda: m.WorkerError("boom"),
    "context_overflow": lambda: m.ContextOverflow("too long"),
    "confinement_refused": lambda: m.ConfinementRefused("pnpm run build",
                                                        "refused"),
    "disk_low": lambda: m.DiskLowSpace("no space"),
    "auth": lambda: m.TerminalAuthFailure("401"),
    "out_of_credits": lambda: m.RateLimitedExit(None, "credits",
                                                out_of_credits=True),
    "rate_limit_exec": lambda: m.RateLimitedExit(None, "limit"),
    "interrupt": lambda: KeyboardInterrupt(),
    "signal": lambda: m.InterruptedBySignal("SIGTERM"),
    "unhandled": lambda: RuntimeError("boom"),
}


async def orchestrate(args, caps, run_dir, st, *a, **k):
    report["run_dir"] = str(st.run_dir)
    copies(st)
    if kind in ("return", "scratch_only"):
        return
    if kind == "finalize":
        return await finalize(st, caps)
    if kind == "needs_answers":
        sys.exit(m.EXIT_NEEDS_ANSWERS)
    if kind == "die":
        m.die("boom")
    if kind == "fork":
        pid = os.fork()
        if pid == 0:
            atexit._run_exitfuncs()        # the child's own exit
            os._exit(0)
        os.waitpid(pid, 0)
        report["template_after_child"] = (
            st.run_dir / m.CLAUDE_CONFIG_TEMPLATE).is_dir()
        return
    if kind == "rate_limit_exec":
        m.time.sleep = lambda s: None
        m.os.execv = at_exec
    raise RAISE[kind]()

m._orchestrate = orchestrate


async def capture_on_exit(repo_root, st, *a, **k):
    # The arms' dependency capture: its worker makes the template again.
    m._worker_dirs(st.run_dir, "dep-capture", None)
m._best_effort_capture_deps = capture_on_exit
sys.argv = ["leerie", "a trivial task", "--run-id", "r19-" + kind.replace("_", "-")]
try:
    m.main()
except SystemExit as e:
    report["code"] = e.code
finally:
    note()
"""

# main()'s handled exits, by the exception its top-level `try` catches:
# every arm is driven (a new arm fails the derivation test below).
ARMS = {
    "WorkerError": ["worker_error"], "ContextOverflow": ["context_overflow"],
    "ConfinementRefused": ["confinement_refused"],
    "DiskLowSpace": ["disk_low"], "TerminalAuthFailure": ["auth"],
    "RateLimitedExit": ["out_of_credits", "rate_limit_exec"],
    "KeyboardInterrupt": ["interrupt"], "InterruptedBySignal": ["signal"],
    "SystemExit": ["needs_answers", "die"], "BaseException": ["unhandled"],
}
# `scratch_only` makes no copy of the credentials: the process owns its run
# by its `State` alone.
KINDS = ["return", "scratch_only", "finalize", "fork"] + sorted(
    k for ks in ARMS.values() for k in ks)


def _mains_handled_exits() -> set[str]:
    import ast
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    main = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "main")
    for node in ast.walk(main):
        if isinstance(node, ast.Try) and any(
                isinstance(c, ast.Call)
                and getattr(c.func, "id", None) == "_orchestrate"
                for b in node.body for c in ast.walk(b)):
            return {ast.unparse(h.type) for h in node.handlers if h.type}
    raise AssertionError("main() has no try around _orchestrate")


def test_every_exit_main_handles_is_driven():
    assert _mains_handled_exits() == set(ARMS)


@pytest.mark.parametrize("kind", KINDS)
def test_no_copy_outlives_the_process_however_main_ends(tmp_path, home,
                                                        kind):
    work = tmp_path / "w"
    repo = work / "repo"
    repo.mkdir(parents=True)
    # HOME is the test's own: the identity main()'s preflight reads is the
    # repository's.
    for argv in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                 ["commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(["git", *argv], cwd=repo, check=True)
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    (bindir / "claude").write_text("#!/bin/sh\necho '{}'\n")
    (bindir / "claude").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "LEERIE_STATE_DIR"}
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    r = subprocess.run([sys.executable, "-c", DRIVER, str(REPO_ROOT),
                        str(work), kind], env=env, capture_output=True,
                       text=True, timeout=300)
    report = json.loads((work / "report.json").read_text())
    run = Path(report["run_dir"])
    assert run.is_dir() or kind == "unhandled", (r.returncode, r.stderr[-2000:])
    for name in ("claude-config", "workers", "measure", "tree-stores",
                 "stores"):
        assert not os.path.lexists(run / name), (name, r.stderr[-2000:])
    assert _no_secret_under(run)
    if kind == "finalize":
        # Dependency capture made the template again after finalize removed
        # the worktrees: only the exit removed it.
        assert report["template_after_finalize"] is True
        assert (run / "run.json").is_file()
    if kind == "fork":
        assert report["template_after_child"] is True
    if kind == "rate_limit_exec":
        assert report["template_at_exec"] is False


def test_a_full_purge_removes_the_read_only_template_with_the_run(
        leerie, tmp_path, home):
    run = tmp_path / "state" / "runs" / "r18-purge"
    _a_run_with_credentials(leerie, run)

    class St:
        run_id = "r18-purge"
        run_dir = run
    leerie._cleanup_on_abnormal_exit(St, full_purge=True)
    assert not os.path.lexists(run)


def test_the_preflight_reap_removes_an_idle_runs_credentials(
        leerie, tmp_path, home):
    """An idle run with no worktrees left still has its copies of the
    credentials removed."""
    runs = tmp_path / "state" / "runs"
    run = runs / "r18-idle"
    _a_run_with_credentials(leerie, run)
    (run / "state.json").write_text("{}")
    for p in [run, *run.rglob("*")]:
        try:
            os.utime(p, (OLD, OLD), follow_symlinks=False)
        except OSError:
            pass
    rep = leerie._reap_idle_worktrees(runs, "self")
    assert rep["errors"] == 0, rep
    assert "r18-idle" in rep["reaped"]
    assert not (run / "claude-config").exists()
    assert not (run / "workers").exists()
    assert (run / "state.json").is_file()


def test_prune_removes_a_died_runs_directory_and_its_credentials(
        leerie, tmp_path, home):
    """The reviewer's driver, inverted: a run that died mid-run (terminal,
    never finalized) is removed by `prune --apply`, its read-only template
    with it, and only then reported removed."""
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    run = root / "runs" / "r18e-died-run"
    _a_run_with_credentials(leerie, run)
    (run / "run.json").write_text(json.dumps(
        {"run_id": run.name, "finished_at": "2026-09-01T00:00:00Z",
         "outcome": "failed"}))
    (run / "state.json").write_text("{}")
    os.utime(run, (OLD, OLD))
    env = {**os.environ, "LEERIE_STATE_DIR": str(root),
           "LEERIE_STATE_HOST_DIR": str(root), "USER_REPO": str(repo)}
    r = subprocess.run(["bash", str(LAUNCHER), "prune", "--apply"],
                       capture_output=True, text=True, env=env, cwd=repo)
    assert r.returncode == 0, r.stderr
    assert "removed 1 terminal run dir" in r.stdout
    assert not os.path.lexists(run)


def test_prune_counts_a_run_removed_only_when_it_is_gone():
    """Structural, because an unremovable directory cannot be made without
    privileges: the launcher's prune removes a run with the owner-aware
    removal and, when that leaves anything, keeps the run (reported, never
    counted removed, its `run.json` kept with it)."""
    script = (REPO_ROOT / "leerie").read_text()
    body = script[script.index("        if terminal and mtime < cutoff"):]
    body = body[:body.index("        else:\n            live.add(d.name)")]
    remove = body.index("if apply and not _rm_owned(d):")
    assert remove < body.index("runs_removed.append(d.name)")
    assert body.index("live.add(d.name)", remove) < body.index(
        "runs_removed.append(d.name)")


def test_the_shell_removal_removes_read_only_dirs(tmp_path):
    top = tmp_path / "t"
    (top / "ro" / "deep").mkdir(parents=True)
    (top / "ro" / "deep" / "f").write_text("x")
    os.chmod(top / "ro" / "deep", 0o500)
    os.chmod(top / "ro", 0o000)
    r = subprocess.run(
        ["bash", "-c", '. "$1"; leerie_rm_owned "$2" && echo gone', "_",
         str(REPO_ROOT / "scripts" / "worktree-lib.sh"), str(top)],
        capture_output=True, text=True)
    assert r.stdout.strip() == "gone", r.stderr
    assert not os.path.lexists(top)


def test_no_run_directory_deleter_ignores_its_errors():
    """Every removal of a run directory, or of the workers' directories and
    the template, goes through the owner-aware removal: no plain
    `rmtree(..., ignore_errors=True)` of them."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    for bad in ("shutil.rmtree(st.run_dir, ignore_errors=True)",
                "shutil.rmtree(d, ignore_errors=True)"):
        assert bad not in src, bad
    launcher = (REPO_ROOT / "leerie").read_text()
    assert "shutil.rmtree(d, ignore_errors=True)" not in launcher
    assert "shutil.rmtree(trash, ignore_errors=True)" not in launcher
