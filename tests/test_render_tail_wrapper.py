"""Tests for `render_tail_wrapper` in scripts/remote/lib.sh.

The helper emits a POSIX-sh wrapper script that runs inside a Fly
Machine. Both leerie's initial-launch tail and the `--resume` smart
router rc=75 pivot (via `tail_with_optional_autofinalize`) use it.
These tests exercise the wrapper in a tmp_path "fake machine"
filesystem (no actual Fly Machine; the script's only dependencies are
local files and `tail -F`).

Coverage:
  - Final-id input → tails directly
  - Pid disappearance → wrapper exits with finalize hint
  - AUTO_FINALIZE_TOKEN env var → token line emitted with final id
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"


def _render(tmp_path: Path) -> str:
    """Run `render_tail_wrapper` and return its stdout (the wrapper script)."""
    r = subprocess.run(
        ["bash", "-c", f". {LIB_SH}; render_tail_wrapper"],
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def _setup_fake_work(tmp_path: Path, run_id: str, *, pid: int | None = None,
                     log_lines: list[str] | None = None) -> Path:
    """Create /work-like directory structure under tmp_path/work/. Returns
    the run dir."""
    work = tmp_path / "work"
    run_dir = work / ".leerie" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "orchestrator.log"
    log.write_text("\n".join(log_lines or []) + "\n")
    if pid is not None:
        (run_dir / "orchestrator.pid").write_text(f"{pid}\n")
    return run_dir


def _run_wrapper(tmp_path: Path, script: str, run_id: str,
                 env_extra: dict | None = None,
                 timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run the wrapper with run_id as $1. The wrapper hard-codes
    /work/... so we use a `sed` rewrite to point at $tmp_path/work for
    the test.

    Uses start_new_session + killpg so background children spawned by
    the wrapper (e.g. ``tail -F``) are killed together with bash.
    Without this, orphaned background processes keep the test's pipes
    open and block communicate() / subprocess.run indefinitely."""
    rewritten = script.replace("/work/", f"{tmp_path}/work/")
    env = {"PATH": "/usr/bin:/bin"}
    if env_extra:
        env.update(env_extra)
    proc = subprocess.Popen(
        ["bash", "-c", rewritten + "\n", "_", run_id],
        env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        stdout, stderr = proc.communicate(timeout=5)
        raise
    return subprocess.CompletedProcess(proc.args, proc.returncode,
                                       stdout, stderr)


# --- emission contract ----------------------------------------------------

def test_render_emits_non_empty_script(tmp_path):
    out = _render(tmp_path)
    assert "tail -F" in out
    assert "orchestrator.log" in out
    assert "orchestrator.pid" in out
    assert "AUTO_FINALIZE_TOKEN" in out


# --- final-id direct tail -------------------------------------------------

def test_final_id_tails_directly_until_pid_dies(tmp_path):
    """Given a final-style run-id and a pid that doesn't exist, the
    wrapper exits promptly with the finalize hint."""
    _setup_fake_work(tmp_path, "feat-x-aaaaaa", pid=999999,
                     log_lines=["[leerie] hello"])
    script = _render(tmp_path)
    r = _run_wrapper(tmp_path, script, "feat-x-aaaaaa")
    assert r.returncode == 0, r.stderr
    assert "orchestrator exited" in r.stderr


# --- auto-finalize token --------------------------------------------------

def test_auto_finalize_token_emitted_when_set(tmp_path):
    """AUTO_FINALIZE_TOKEN env var → wrapper prints "${TOKEN}${final_id}"
    on its last stderr line for the host caller to grep."""
    _setup_fake_work(tmp_path, "feat-z-cccccc", pid=999999,
                     log_lines=["[leerie] go"])
    script = _render(tmp_path)
    r = _run_wrapper(tmp_path, script, "feat-z-cccccc",
                     env_extra={"AUTO_FINALIZE_TOKEN": "<<TOK>>"})
    assert r.returncode == 0, r.stderr
    assert "<<TOK>>feat-z-cccccc" in r.stderr


def test_auto_finalize_token_absent_when_unset(tmp_path):
    """No token env var → no token line in stderr."""
    _setup_fake_work(tmp_path, "feat-q-dddddd", pid=999999,
                     log_lines=["[leerie] go"])
    script = _render(tmp_path)
    r = _run_wrapper(tmp_path, script, "feat-q-dddddd")
    assert r.returncode == 0, r.stderr
    assert "<<" not in r.stderr.split("\n")[-2]  # no token sentinel


# --- /proc cross-check: watcher keeps watching when pid file is stale -----

def test_watcher_keeps_watching_when_proc_scan_finds_live(tmp_path):
    """Stale-pid contagion regression test.

    Fixture: pid file points to a dead pid, BUT a live process exists
    whose argv contains both the `orchestrator/leerie.py` anchor and
    the run-id. The watcher must NOT print the finalize banner while
    that process is alive — only after it dies. See DESIGN §6 *Single
    owner per run dir* — stale-pid contagion.
    """
    import sys as _sys
    if _sys.platform != "linux":
        import pytest
        pytest.skip("/proc cross-check only meaningful on Linux")

    run_id = "feat-watcher-scan-fixture-pmt7351"
    _setup_fake_work(tmp_path, run_id, pid=999999,
                     log_lines=["[leerie] hello"])
    script = _render(tmp_path)

    fake_orch_path = str(tmp_path / "orchestrator" / "leerie.py")
    sleeper = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(30)",
         fake_orch_path, "--run-id", run_id],
    )
    proc = None
    try:
        rewritten = script.replace("/work/", f"{tmp_path}/work/")
        proc = subprocess.Popen(
            ["bash", "-c", rewritten + "\n", "_", run_id],
            env={"PATH": "/usr/bin:/bin"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        # Watcher polls every 2 s. Give it time for one or two
        # iterations to confirm it's not bailing on the stale pid.
        time.sleep(5)
        # Watcher should still be running — the /proc scan finds the
        # sleeper, so liveness is asserted regardless of the pid file.
        assert proc.poll() is None, (
            "watcher exited despite live /proc match; stderr so far: "
            f"{proc.stderr.read() if proc.stderr else '?'}"
        )
        # Now kill the sleeper. Wait long enough for the watcher to
        # poll (2 s) and observe the disappearance.
        sleeper.terminate()
        sleeper.wait(timeout=5)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Kill the entire process group — bash's background
            # `(tail -F ...) &` child inherits the test's pipes and
            # keeps them open after bash dies, so proc.kill() alone
            # would leave communicate() blocked on EOF forever.
            os.killpg(proc.pid, signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=5)
            raise AssertionError(
                f"watcher hung after live process died; stderr: {stderr!r}"
            )
        assert proc.returncode == 0, stderr
        assert "orchestrator exited" in stderr
    finally:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        if sleeper.poll() is None:
            sleeper.terminate()
            try:
                sleeper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sleeper.kill()
                sleeper.wait()


# ----- liveness-scan cost (regression) --------------------------------------

def test_liveness_scan_does_not_fork_per_process(tmp_path):
    """`_orch_is_alive` must prefilter with ONE grep, not fork `tr` per
    /proc entry.

    The per-process form cost 11.8s at 789 processes (measured: sys 9.0s,
    almost entirely fork overhead) and the watcher re-runs it every 2 seconds
    — so the "orchestrator exited" banner the host greps for was delayed by a
    full scan, and the four functional tests above timed out on any host with
    a few hundred processes. They passed on an idle machine, which is why this
    was mistaken for a flaky-environment problem across several review passes.

    Source-coupled deliberately: the functional tests only expose the
    regression under real load, so on an idle CI box they would pass against
    the slow form. This assertion holds regardless of load.
    """
    script = _render(tmp_path)
    assert 'grep -la "orchestrator/leerie.py" /proc/[0-9]*/cmdline' in script, (
        "the /proc scan must prefilter with a single grep")
    assert "for _cmd in /proc/[0-9]*/cmdline" not in script, (
        "iterating the raw glob reintroduces one fork per process")


def test_liveness_scan_completes_promptly(tmp_path):
    """Functional companion: one scan against the real /proc must be fast.

    Bound is generous (3s) so it cannot flake on a slow-but-correct host,
    while the fork-per-entry form exceeds it on any machine with more than a
    couple hundred processes — the condition under which the regression
    actually bites.
    """
    import time

    script = _render(tmp_path)
    # Extract just the function and call it once with a run-id that matches
    # nothing, forcing a full scan rather than an early return.
    harness = script.replace("/work/", f"{tmp_path}/work/")
    probe = (
        f"ID=no-such-run-id-xyz\nORCH_PID=\n"
        + harness[harness.index("_orch_is_alive() {"):harness.index("\nif [ -n \"$ORCH_PID\" ]")]
        + "\n_orch_is_alive; echo \"rc=$?\"\n"
    )
    t = time.time()
    r = subprocess.run(["bash", "-c", probe], capture_output=True, text=True,
                       timeout=60)
    elapsed = time.time() - t
    assert "rc=1" in r.stdout, f"scan should report dead, got: {r.stdout!r}"
    assert elapsed < 3.0, (
        f"one liveness scan took {elapsed:.1f}s against "
        f"{len(list(__import__('pathlib').Path('/proc').glob('[0-9]*')))} "
        "processes — the scan is forking per process again")
