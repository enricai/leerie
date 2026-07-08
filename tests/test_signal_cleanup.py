"""Tests for OS-signal cleanup (DESIGN §6 / DESIGN §14).

Coverage:
- `_install_signal_handlers` registers handlers for SIGTERM (and SIGHUP
  on POSIX) without disturbing SIGINT (which keeps Python's default).
- The handler raises `InterruptedBySignal`.
- `_cleanup_on_abnormal_exit` removes worktrees; with `full_purge=True`
  it also removes the run dir.
- Source-text pins on main()'s try/except/finally structure ensure the
  per-exception `full_purge` flag selection is preserved across refactors.
"""
from __future__ import annotations

import inspect
import os
import re
import signal as _signal
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


# --- InterruptedBySignal --------------------------------------------------

def test_interrupted_by_signal_is_base_exception(leerie):
    """Must subclass BaseException (not Exception) so the broad
    `except Exception` handlers inside orchestrate() don't swallow it."""
    assert issubclass(leerie.InterruptedBySignal, BaseException)
    assert not issubclass(leerie.InterruptedBySignal, Exception)


# --- _install_signal_handlers --------------------------------------------

def test_install_signal_handlers_registers_sigterm(leerie, monkeypatch):
    """SIGTERM gets a custom handler installed."""
    installed: dict = {}

    def fake_signal(signum, handler):
        installed[signum] = handler
    monkeypatch.setattr(leerie.signal, "signal", fake_signal)
    leerie._install_signal_handlers()
    assert _signal.SIGTERM in installed


def test_install_signal_handlers_registers_sighup_on_posix(leerie, monkeypatch):
    """SIGHUP gets a handler too, when available."""
    if not hasattr(_signal, "SIGHUP"):
        pytest.skip("SIGHUP not available on this platform")
    installed: dict = {}

    def fake_signal(signum, handler):
        installed[signum] = handler
    monkeypatch.setattr(leerie.signal, "signal", fake_signal)
    leerie._install_signal_handlers()
    assert _signal.SIGHUP in installed


def test_install_signal_handlers_does_not_touch_sigint(leerie, monkeypatch):
    """SIGINT must keep Python's default (KeyboardInterrupt) — not
    intercepted by InterruptedBySignal. main() handles KeyboardInterrupt
    separately for the full-purge path."""
    installed: dict = {}

    def fake_signal(signum, handler):
        installed[signum] = handler
    monkeypatch.setattr(leerie.signal, "signal", fake_signal)
    leerie._install_signal_handlers()
    assert _signal.SIGINT not in installed


def test_signal_handler_raises_interrupted_by_signal(leerie, monkeypatch):
    """When the installed SIGTERM handler is invoked, it raises
    InterruptedBySignal — that's what bubbles up to main()."""
    handlers: dict = {}

    def fake_signal(signum, handler):
        handlers[signum] = handler
    monkeypatch.setattr(leerie.signal, "signal", fake_signal)
    leerie._install_signal_handlers()
    handler = handlers[_signal.SIGTERM]
    with pytest.raises(leerie.InterruptedBySignal):
        handler(_signal.SIGTERM, None)


# --- _cleanup_on_abnormal_exit -------------------------------------------

class _FakeState:
    """Minimal State stand-in: only `run_id` and `run_dir` are read by
    `_cleanup_on_abnormal_exit`."""
    def __init__(self, run_id: str, run_dir: Path):
        self.run_id = run_id
        self.run_dir = run_dir


def test_cleanup_handles_none_state_gracefully(leerie):
    """Defensive: cleanup early-returns on a None state rather than
    raising. Used when main() bails before constructing State."""
    leerie._cleanup_on_abnormal_exit(None, full_purge=False)  # must not raise


def test_cleanup_removes_worktrees_dir(leerie, tmp_path, monkeypatch):
    """_cleanup_on_abnormal_exit calls `git worktree remove --force` for
    each subdir of run_dir/worktrees/. Test by stubbing subprocess.run
    and confirming the calls."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "worktrees" / "staging").mkdir(parents=True)
    (run_dir / "worktrees" / "feat-001").mkdir(parents=True)
    st = _FakeState(run_id, run_dir)

    calls: list[list[str]] = []
    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(leerie.subprocess, "run", fake_run)

    leerie._cleanup_on_abnormal_exit(st, full_purge=False)

    # Two worktree-remove calls + one prune.
    remove_calls = [c for c in calls if c[:3] == ["git", "worktree", "remove"]]
    assert len(remove_calls) == 2
    assert any(c for c in calls if c == ["git", "worktree", "prune"])


def test_cleanup_full_purge_deletes_run_dir(leerie, tmp_path, monkeypatch):
    """With full_purge=True, the run_dir is removed via shutil.rmtree."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text("{}")
    st = _FakeState(run_id, run_dir)

    monkeypatch.setattr(leerie.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""))

    assert run_dir.exists()
    leerie._cleanup_on_abnormal_exit(st, full_purge=True)
    assert not run_dir.exists(), (
        "full_purge=True must remove the run_dir entirely"
    )


def test_cleanup_rm_rf_fallback_when_git_leaves_dir(leerie, tmp_path,
                                                    monkeypatch):
    """When `git worktree remove` returns nonzero (or zero) but does NOT
    actually delete the directory — e.g. git already pruned the worktree
    from its registry on a previous pass — the cleanup must fall back to
    rm -rf so the surviving directory doesn't block --resume's
    new-worktree.sh from re-creating the worktree at the same path.

    Observed in finalmemoriam on 2026-05-28: an overnight run timed out
    on node_modules under the old 30s cap, cleanup logged a failure,
    git later pruned its registry, and the surviving worktree dir
    blocked --resume the next morning with
    `fatal: '...' already exists`."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    wt_a = run_dir / "worktrees" / "feat-001"
    wt_a.mkdir(parents=True)
    # Put something in the worktree (simulates leftover node_modules).
    (wt_a / "leftover.txt").write_text("stale")
    st = _FakeState(run_id, run_dir)

    # Simulate git's behavior in the failure scenario: subprocess.run
    # succeeds (no exception) but git does nothing on disk (returns
    # nonzero because the worktree isn't tracked).
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "fatal: not a worktree")
    monkeypatch.setattr(leerie.subprocess, "run", fake_run)

    assert wt_a.exists()
    leerie._cleanup_on_abnormal_exit(st, full_purge=False)
    assert not wt_a.exists(), (
        "cleanup must rm -rf the worktree dir when git worktree remove "
        "leaves it behind, otherwise --resume's new-worktree.sh will "
        "fail with 'already exists' when it tries to re-create the "
        "worktree at the same path."
    )


def test_cleanup_rm_rf_fallback_after_timeout(leerie, tmp_path, monkeypatch):
    """Mirror of the above for the timeout case: subprocess.TimeoutExpired
    is raised mid-removal, but the directory survives (with partial
    contents). Cleanup must still fall back to rm -rf so the surviving
    dir doesn't block --resume."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    wt_a = run_dir / "worktrees" / "feat-001"
    wt_a.mkdir(parents=True)
    (wt_a / "leftover.txt").write_text("stale")
    st = _FakeState(run_id, run_dir)

    def fake_run(cmd, **kwargs):
        # Only timeout for the worktree-remove call; let prune succeed.
        if cmd[:3] == ["git", "worktree", "remove"]:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(leerie.subprocess, "run", fake_run)

    leerie._cleanup_on_abnormal_exit(st, full_purge=False)
    assert not wt_a.exists(), (
        "cleanup must rm -rf after a TimeoutExpired so the surviving "
        "dir doesn't persist across runs."
    )


def test_cleanup_rm_rf_skips_when_path_escapes_sandbox(leerie, tmp_path,
                                                      monkeypatch):
    """Belt-and-suspenders: the rm -rf fallback must verify the
    resolved path lies within the worktrees dir before deleting. If a
    refactor or symlink ever caused entry.resolve().parent to escape
    the sandbox, the rm would be a no-op rather than a destructive
    misfire."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    worktrees_dir = run_dir / "worktrees"
    worktrees_dir.mkdir(parents=True)
    # Create a real file outside the sandbox.
    outside = tmp_path / "outside_target"
    outside.mkdir()
    (outside / "important.txt").write_text("do not delete")
    # Symlink from inside the worktrees dir to the outside path.
    sym = worktrees_dir / "feat-001"
    sym.symlink_to(outside)
    st = _FakeState(run_id, run_dir)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "")
    monkeypatch.setattr(leerie.subprocess, "run", fake_run)

    leerie._cleanup_on_abnormal_exit(st, full_purge=False)
    # The symlink itself may or may not survive (resolve depends on
    # what counts as a directory iteration), but the OUTSIDE target
    # must survive — that's the load-bearing invariant.
    assert outside.exists()
    assert (outside / "important.txt").exists()
    assert (outside / "important.txt").read_text() == "do not delete"


def test_cleanup_no_purge_preserves_run_dir(leerie, tmp_path, monkeypatch):
    """full_purge=False leaves the run_dir intact (worktrees may be
    removed, but state.json and the dir itself survive)."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "worktrees").mkdir(parents=True)
    (run_dir / "state.json").write_text("{}")
    st = _FakeState(run_id, run_dir)

    monkeypatch.setattr(leerie.subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""))

    leerie._cleanup_on_abnormal_exit(st, full_purge=False)
    assert run_dir.exists(), "full_purge=False must preserve the run_dir"
    assert (run_dir / "state.json").exists(), "state.json must survive non-purge cleanup"


def test_cleanup_full_purge_deletes_branches(leerie, tmp_path, monkeypatch):
    """full_purge=True invokes `git for-each-ref` to enumerate branches
    and `git branch -D` to delete each one."""
    run_id = "feat-x-aaa111"
    run_dir = tmp_path / "runs" / run_id
    run_dir.mkdir(parents=True)
    st = _FakeState(run_id, run_dir)

    branches_to_delete = [
        f"leerie/runs/{run_id}",
        f"leerie/subtasks/{run_id}/feat-001",
    ]
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "for-each-ref"]:
            # The cleanup walks two globs: refs/heads/leerie/runs/<id>
            # (the run branch, exact match) and refs/heads/leerie/subtasks/<id>/
            # (the subtask-branch prefix). Distinguish by the runs/ vs subtasks/
            # segment so each glob returns the matching branch.
            glob = cmd[3]
            if glob == f"refs/heads/leerie/runs/{run_id}":
                return subprocess.CompletedProcess(cmd, 0, f"leerie/runs/{run_id}\n", "")
            if glob == f"refs/heads/leerie/subtasks/{run_id}/":
                return subprocess.CompletedProcess(cmd, 0, f"leerie/subtasks/{run_id}/feat-001\n", "")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(leerie.subprocess, "run", fake_run)

    leerie._cleanup_on_abnormal_exit(st, full_purge=True)

    delete_calls = [c for c in calls if c[:3] == ["git", "branch", "-D"]]
    assert len(delete_calls) == 2, f"expected 2 branch deletes, got {delete_calls}"


# --- main() try/except/finally pinning -----------------------------------

def _main_body() -> str:
    """Extract main()'s body from leerie.py source."""
    src = LEERIE_PY.read_text()
    m = re.search(
        r"^def main\(\) -> None:\n(.*?)(?=^(?:def |class |if __name__))",
        src, re.DOTALL | re.MULTILINE,
    )
    assert m
    return m.group(1)


def test_main_calls_install_signal_handlers():
    body = _main_body()
    assert "_install_signal_handlers()" in body


def test_main_keyboard_interrupt_no_full_purge():
    """SIGINT (KeyboardInterrupt) → full_purge=False. Pin the per-exception
    flag selection so a refactor can't silently regress Ctrl-C from
    'preserve and resume' back to the old 'throw it away' behavior
    (DESIGN §6 *Cleanup on abnormal exit*: every abnormal exit
    preserves state and branches; only worktrees are torn down).

    Anchor on outer-try indentation (4 spaces) so an inner
    `except KeyboardInterrupt` nested under a deeper indent — e.g.
    the RateLimitedExit arm's sleep-interrupt guard — doesn't shadow
    the outer clause."""
    body = _main_body()
    # Find the OUTER except KeyboardInterrupt block (4-space indent).
    m = re.search(
        r"\n    except KeyboardInterrupt:(.*?)(?=^\s*except |^\s*finally:)",
        body, re.DOTALL | re.MULTILINE,
    )
    assert m, ("could not locate outer except KeyboardInterrupt block "
               "in main() at the 4-space indent")
    block = m.group(1)
    assert "full_purge = False" in block
    assert "full_purge = True" not in block


def test_main_rate_limit_sleep_catches_keyboard_interrupt():
    """Ctrl-C during the auto-resume sleep must produce the friendly
    'state preserved' log message, not a silent exit. The outer
    KeyboardInterrupt arm of main() is reached *outside* the
    RateLimitedExit arm — when the user Ctrl-C's while we're inside
    `time.sleep` within the RateLimitedExit arm, the KeyboardInterrupt
    would escape to Python's default handler without our friendly
    message unless it's caught locally. Pin that the local catch
    exists."""
    body = _main_body()
    # Find the OUTER except RateLimitedExit block (4-space indent).
    # The lookahead anchors on the same outer-try indent to avoid
    # truncating at inner `except BaseException` clauses nested inside
    # the arm (e.g. the cleanup-failure guard).
    m = re.search(
        r"\n    except RateLimitedExit[^:]*:(.*?)(?=\n    except |\n    finally:)",
        body, re.DOTALL,
    )
    assert m, ("could not locate outer except RateLimitedExit block in "
               "main() at the 4-space indent")
    block = m.group(1)
    # The block must contain a local KeyboardInterrupt catch wrapping
    # time.sleep — otherwise Ctrl-C during the wait silently kills the
    # process without the user-facing "state preserved" message.
    assert "time.sleep" in block
    assert "except KeyboardInterrupt" in block, (
        "RateLimitedExit arm must locally catch KeyboardInterrupt "
        "around time.sleep so Ctrl-C during the auto-resume wait "
        "produces the standard 'state preserved' message rather than "
        "a silent exit."
    )


def test_main_interrupted_by_signal_no_full_purge():
    """SIGTERM/SIGHUP → full_purge=False (preserve for resume)."""
    body = _main_body()
    m = re.search(
        r"except InterruptedBySignal[^:]*:(.*?)(?=^\s*except |^\s*finally:)",
        body, re.DOTALL | re.MULTILINE,
    )
    assert m, "could not locate except InterruptedBySignal block in main()"
    block = m.group(1)
    assert "full_purge = False" in block


def test_main_worker_error_no_full_purge():
    """WorkerError → preserve for resume (user can fix the issue and continue)."""
    body = _main_body()
    m = re.search(
        r"except WorkerError[^:]*:(.*?)(?=^\s*except |^\s*finally:)",
        body, re.DOTALL | re.MULTILINE,
    )
    assert m, "could not locate except WorkerError block in main()"
    block = m.group(1)
    assert "full_purge = False" in block


def test_main_finally_calls_cleanup():
    body = _main_body()
    assert "_cleanup_on_abnormal_exit(st, full_purge=full_purge)" in body


def test_main_system_exit_not_treated_as_unhandled():
    """`die()` raises SystemExit. main() must catch it explicitly (before
    the catch-all `except BaseException`) and re-raise without logging
    'unhandled exception' — die() is the *clean* exit mechanism and the
    user already got the right error message."""
    body = _main_body()
    # Look for an `except SystemExit` block that appears before the
    # catch-all `except BaseException` block. Anchor on the outer
    # try-block indentation (4 spaces) so an inner `except BaseException`
    # nested under a deeper indent — e.g. the RateLimitedExit arm's
    # cleanup-failure guard — doesn't shadow the outer clause.
    sysexit_pos = body.find("\n    except SystemExit")
    base_pos = body.find("\n    except BaseException")
    assert sysexit_pos != -1, (
        "main() must explicitly catch SystemExit so die() calls aren't "
        "mistakenly logged as unhandled exceptions"
    )
    assert base_pos != -1, (
        "main() must have a catch-all except BaseException clause at "
        "the outer try-block indent"
    )
    assert sysexit_pos < base_pos, (
        "except SystemExit must appear BEFORE except BaseException — "
        "otherwise the catch-all matches first (BaseException is the "
        "superclass) and SystemExit gets the unhandled-exception path"
    )


# --- Subprocess-tree termination (DESIGN §6 "Worker subtree termination") -
#
# Pin the discipline that satisfies the design contract: every subprocess
# spawn passes start_new_session=True (isolating the worker into its own
# POSIX session); every exception-cleanup path routes through
# _terminate_proc_tree (which combines killpg with a PPID walk to reach
# detached descendants); and `_invoke` wires a _DescendantTracker that
# observes the worker's descendants throughout its lifetime so they can
# be reaped even on a clean exit (Claude Code's run_in_background
# subprocesses outlive the worker and reparent to PID 1).

def test_every_subprocess_spawn_uses_start_new_session():
    """Static: every `asyncio.create_subprocess_exec` in leerie.py must
    pass `start_new_session=True` so the worker is isolated into its own
    POSIX session. This is required so that on cleanup, `os.killpg(proc.pid)`
    does not accidentally signal the orchestrator's own process group."""
    src = LEERIE_PY.read_text()
    # Find every create_subprocess_exec(...) call. Match across lines
    # via DOTALL; bound on the closing `)` at the natural call indent.
    calls = re.findall(
        r"asyncio\.create_subprocess_exec\((.*?)\n    \)",
        src, re.DOTALL,
    )
    assert calls, ("expected at least one create_subprocess_exec call "
                   "in leerie.py")
    for i, body in enumerate(calls):
        assert "start_new_session=True" in body, (
            f"create_subprocess_exec call #{i + 1} is missing "
            f"start_new_session=True. Without session isolation, "
            f"`os.killpg(proc.pid, ...)` in the cleanup path could "
            f"signal the orchestrator's own process group. "
            f"DESIGN §6 'Worker subtree termination on every exit'. "
            f"Call body:\n{body}"
        )


def test_no_bare_proc_kill_outside_terminate_proc_tree(leerie):
    """Static: `proc.kill()` (which kills only the direct child PID)
    must not appear anywhere in leerie.py. Every subprocess-cleanup path
    must instead route through `_terminate_proc_tree`, which combines
    `killpg` on the leader's group with a PPID walk to reach detached
    descendants (Claude Code's Bash tool runs in its own POSIX session,
    so `killpg(claude_p_pgid)` alone does not reach it).

    A regression that puts `proc.kill()` back into `run_proc` or
    `_invoke`'s exception handlers would silently re-leak the
    detached descendants. This test pins that against drift."""
    src = LEERIE_PY.read_text()
    # Locate _terminate_proc_tree's body so we can exclude it from
    # the scan (defensive — the current implementation doesn't call
    # proc.kill() either, but we don't want this test to lock the
    # helper's internal mechanism).
    helper_src = inspect.getsource(leerie._terminate_proc_tree)
    src_outside_helper = src.replace(helper_src, "")
    matches = re.findall(r"\bproc\.kill\(\)", src_outside_helper)
    assert not matches, (
        f"found {len(matches)} bare proc.kill() call(s) outside "
        f"_terminate_proc_tree. Every subprocess cleanup path must "
        f"route through _terminate_proc_tree to reach descendants in "
        f"detached POSIX sessions (Claude Code's Bash tool spawns its "
        f"command in its own session, so `killpg` on the worker's "
        f"group does not reach it)."
    )


def test_run_proc_and_invoke_exception_handlers_call_terminate_proc_tree():
    """Static: both subprocess wrappers' `except` blocks must invoke
    `_terminate_proc_tree`. Source-pin to catch the case where someone
    refactors and accidentally drops one of the four handlers."""
    src = LEERIE_PY.read_text()
    # `run_proc`: from its def to the matching `return subprocess.CompletedProcess`
    m_run = re.search(
        r"async def run_proc\(.*?\n    return subprocess\.CompletedProcess",
        src, re.DOTALL,
    )
    assert m_run, "could not locate run_proc body in leerie.py"
    run_proc_body = m_run.group(0)
    # `_invoke` is a top-level `async def`. Bound on the next top-level
    # def (also flush-left) so we don't bleed into _capture_call or
    # claude_p downstream.
    m_inv = re.search(
        r"\nasync def _invoke\(.*?\n(?=async def |def )",
        src, re.DOTALL,
    )
    assert m_inv, "could not locate _invoke body in leerie.py"
    invoke_body = m_inv.group(0)

    for label, body in [("run_proc", run_proc_body), ("_invoke", invoke_body)]:
        # Each function must terminate the proc tree on TimeoutError
        # and on the catch-all BaseException. Count occurrences rather
        # than slicing nested blocks (which is brittle to inner
        # try/except inside _invoke's coroutines like _read_stream).
        # Both regexes allow non-await statements (e.g. a synchronous
        # watchdog_task.cancel()) between the `except` line and the
        # _terminate_proc_tree call — the invariant being pinned is
        # "the handler calls _terminate_proc_tree", not "_terminate is
        # the literal next line."
        timeout_present = re.search(
            r"except asyncio\.TimeoutError:.*?\n\s*await _terminate_proc_tree\(proc\)",
            body, re.DOTALL,
        )
        base_present = re.search(
            r"except BaseException:.*?\n\s*await _terminate_proc_tree\(proc\)",
            body, re.DOTALL,
        )
        assert timeout_present, (
            f"{label}'s `except asyncio.TimeoutError` handler must "
            f"`await _terminate_proc_tree(proc)`."
        )
        assert base_present, (
            f"{label}'s `except BaseException` handler must call "
            f"`await _terminate_proc_tree(proc)` to terminate the "
            f"worker's whole process group before re-raising."
        )


@pytest.mark.skipif(
    os.name == "nt",
    reason="start_new_session is a no-op on Windows; the POSIX "
           "process-group semantics this test exercises don't apply.",
)
def test_terminate_proc_tree_reaps_grandchildren(leerie):
    """Behavioral: spawn a subprocess with start_new_session=True that
    itself launches a long-running grandchild, then call
    _terminate_proc_tree and assert the grandchild is gone.

    Static tests above pin the spelling (`start_new_session=True`,
    `_terminate_proc_tree` calls); this one pins the semantics — the
    actual property the DESIGN §6 contract promises."""
    import asyncio
    import time

    async def _run():
        # Parent shell: spawn a `sleep 60` in the background, print its
        # PID on stdout, then wait. When we kill the group, the sleep
        # must die too. `exec sleep` would replace the parent — we want
        # a *separate* grandchild PID to verify the group kill reaches
        # past the immediate child.
        script = (
            "sleep 60 & "
            "child=$!; "
            "echo $child; "
            # Hold the parent alive so the group exists when we signal
            # it; without this the parent exits after the background
            # spawn and the test races.
            "wait $child"
        )
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c", script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        # Read the grandchild PID off the parent's stdout.
        line = await proc.stdout.readline()
        grandchild_pid = int(line.strip())
        # Sanity: parent and grandchild are alive.
        assert _pid_alive(proc.pid), "parent died before test could run"
        assert _pid_alive(grandchild_pid), "grandchild never started"

        try:
            await leerie._terminate_proc_tree(proc)
        finally:
            # Safety net: if the helper somehow didn't reap the
            # grandchild, do it ourselves so a failing test doesn't
            # leak a 60-second sleeper.
            if _pid_alive(grandchild_pid):
                try:
                    os.kill(grandchild_pid, _signal.SIGKILL)
                except ProcessLookupError:
                    pass

        # The parent must be reaped.
        assert proc.returncode is not None, "parent was not reaped"
        # The grandchild must be gone within a small window. The
        # helper's grace is _PROC_TREE_GRACE_SEC (2s) plus the
        # SIGKILL pass — give 3s total for the kernel to flush.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not _pid_alive(grandchild_pid):
                break
            await asyncio.sleep(0.05)
        assert not _pid_alive(grandchild_pid), (
            f"grandchild PID {grandchild_pid} survived "
            f"_terminate_proc_tree — the process-group kill is not "
            f"reaching past the immediate child. This is the DESIGN "
            f"§6 'Worker subtree termination' contract failing."
        )

    asyncio.run(_run())


def _pid_alive(pid: int) -> bool:
    """True if a process with the given PID exists and we can signal
    it. `os.kill(pid, 0)` is the POSIX idiom — no signal is delivered;
    it only does the permission/existence check."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # The PID exists but we don't own it — for our test's purposes
        # (it's a process we spawned ourselves) this should never
        # happen, but treat it as "alive" to avoid false negatives.
        return True


# --- PPID-walk + detached-session reaping ----------------------------------

@pytest.mark.skipif(
    os.name == "nt",
    reason="PPID walk + start_new_session semantics are POSIX-only.",
)
def test_terminate_proc_tree_reaps_detached_session_grandchildren(leerie):
    """Leerie's worker (`claude -p`) spawns the Claude Code Bash tool via
    `spawn({detached: true})`, which puts the Bash tool subprocess into a
    NEW POSIX session — its PGID == its own PID, distinct from the
    worker's PGID. `os.killpg(worker_pgid)` does NOT reach it.

    The helper must instead walk the PPID chain (which stays intact while
    the parent lives) and signal every descendant by PID.

    This test exercises that exact shape: a "worker" Python process whose
    immediate child is in a *new session*, and the child has grandchildren.
    The helper must reach all the way down."""
    import asyncio
    import subprocess
    import time

    # Mimic Claude Code's spawn({detached: true}) by having the worker
    # spawn its child with start_new_session=True. Leerie wouldn't use
    # this pattern for its own subprocesses, but `claude -p` does, and
    # `_terminate_proc_tree` must handle it.
    WORKER_PYTHON = (
        "import subprocess, time\n"
        "child = subprocess.Popen(\n"
        "    ['bash', '-c', 'sleep 47474 & sleep 47474 & wait'],\n"
        "    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    start_new_session=True,\n"
        ")\n"
        "time.sleep(300)\n"
    )

    async def _run():
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", WORKER_PYTHON,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait for the detached bash + its sleeps to appear
        for _ in range(40):
            await asyncio.sleep(0.1)
            descs = leerie._enumerate_descendants(proc.pid)
            if len(descs) >= 3:  # bash + 2 sleeps
                break
        else:
            try:
                proc.kill()
                await proc.wait()
            except BaseException:
                pass
            pytest.fail("worker never produced expected descendants")

        # Confirm the detached child has its own PGID
        detached_pids = [d for d in descs
                         if subprocess.run(
                             ["ps", "-p", str(d), "-o", "command="],
                             capture_output=True, text=True
                         ).stdout.strip().startswith("bash")]
        assert detached_pids, "no detached bash found among descendants"
        detached_pid = detached_pids[0]
        detached_pgid = int(subprocess.run(
            ["ps", "-p", str(detached_pid), "-o", "pgid="],
            capture_output=True, text=True
        ).stdout.strip())
        worker_pgid = proc.pid  # start_new_session=True ⇒ PGID == PID
        assert detached_pgid != worker_pgid, (
            "test setup invalid: detached child is in the same PGID as "
            "the worker. This test must exercise the detached-session "
            "case, which requires the child to be in a NEW session."
        )

        sleep_descs = [d for d in descs if subprocess.run(
            ["ps", "-p", str(d), "-o", "command="],
            capture_output=True, text=True
        ).stdout.strip().startswith("sleep 47474")]
        assert len(sleep_descs) == 2, f"expected 2 sleeps, got {len(sleep_descs)}"

        # Run the fix. All descendants must die — including the ones in
        # the detached session that killpg(worker_pgid) cannot reach.
        try:
            await leerie._terminate_proc_tree(proc)
        finally:
            # Safety net so a broken helper doesn't leak a 5-minute sleep
            for d in descs:
                try: os.kill(d, _signal.SIGKILL)
                except ProcessLookupError: pass

        # All sleeps must be gone
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            alive = [d for d in sleep_descs if _pid_alive(d)]
            if not alive:
                break
            await asyncio.sleep(0.05)
        survivors = [d for d in sleep_descs if _pid_alive(d)]
        assert not survivors, (
            f"detached-session sleeps survived _terminate_proc_tree: "
            f"{survivors}. The PPID-walk did not reach descendants whose "
            f"PGID differs from the worker's. Claude Code's Bash tool "
            f"runs in its own session, so `os.killpg(worker_pgid)` "
            f"alone cannot reach it — the cleanup helper must combine "
            f"killpg with a PPID-walk."
        )

    asyncio.run(_run())


# --- Success-path cleanup via _DescendantTracker ---------------------------

@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only test.",
)
def test_descendant_tracker_reaps_orphaned_backgrounded_subprocess(leerie):
    """Even on a clean leader exit, Claude Code's `run_in_background:
    true` Bash tool calls leak — the backgrounded subprocesses are spawned
    in detached POSIX sessions and reparent to PID 1 the moment their
    immediate parent exits.

    A naive post-exit PPID-walk finds nothing (the orphans are no longer
    descendants of the dead leader). `_DescendantTracker` solves this by
    polling `_enumerate_descendants` THROUGHOUT the leader's life and
    accumulating every PID it ever sees. At exit, the accumulated set
    is SIGKILLed — catching the now-orphaned children."""
    import asyncio
    import subprocess

    async def _run():
        # Leader backgrounds a sleep then waits briefly so the tracker has
        # at least one poll cycle to observe the sleep before the leader
        # exits.
        proc = await asyncio.create_subprocess_exec(
            "bash", "-c",
            "sleep 38383 < /dev/null > /dev/null 2>&1 & "
            "echo $! ; "
            "sleep 1",  # keep parent alive 1s so tracker's 0.5s poll catches the sleep
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        tracker = leerie._DescendantTracker(proc.pid)
        tracker.start()
        # Read the sleep PID off stdout
        line = await proc.stdout.readline()
        sleep_pid = int(line.strip())
        # Let the leader exit cleanly
        await proc.wait()
        # At this point sleep_pid should be orphaned (PPID=1) but still
        # alive. The tracker should have observed it during its ~0.5s
        # poll while the parent was alive.
        await asyncio.sleep(0.1)  # let kernel reparent
        # stop_and_reap must kill the orphaned sleep
        leaked = await tracker.stop_and_reap()
        assert leaked >= 1, (
            f"tracker reaped {leaked} descendants — expected at least 1 "
            f"(the backgrounded sleep that became an orphan when its "
            f"parent exited). The tracker did not observe the sleep "
            f"during its polling window."
        )
        # Verify the sleep actually died
        await asyncio.sleep(0.2)
        assert not _pid_alive(sleep_pid), (
            f"sleep PID {sleep_pid} survived tracker.stop_and_reap. The "
            f"tracker recorded the PID but SIGKILL didn't deliver — "
            f"likely a permission or signal-delivery bug."
        )

    try:
        asyncio.run(_run())
    finally:
        # Safety net
        subprocess.run(["pkill", "-9", "-f", "sleep 38383"], capture_output=True)


# --- Module-level helper unit tests ----------------------------------------

@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only test.",
)
def test_enumerate_descendants_returns_indirect_children(leerie):
    """`_enumerate_descendants(root_pid)` must walk transitively, not just
    list direct children. Spawn a 3-deep chain and assert all 3 are
    found."""
    import subprocess
    # outer bash → (sub-bash backgrounded with &) → sleep
    # The outer bash MUST keep running (its trailing `wait` is what holds it
    # alive) so the PPID chain stays intact while we measure. Without the
    # `& wait` shape, outer bash would exec into the sub-bash via tail-call
    # optimization and the chain would only be 2-deep.
    leader = subprocess.Popen(
        ["bash", "-c", "bash -c 'sleep 28282 & wait' & wait"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    import time
    time.sleep(1.5)
    try:
        descs = leerie._enumerate_descendants(leader.pid)
        # Should find: inner bash (level 1), sleep (level 2)
        assert len(descs) >= 2, (
            f"_enumerate_descendants found only {len(descs)} descendants "
            f"(expected at least 2 — inner bash + sleep). Walk is not "
            f"transitive."
        )
    finally:
        leader.kill()
        subprocess.run(["pkill", "-9", "-f", "sleep 28282"], capture_output=True)
        leader.wait()


def test_enumerate_descendants_returns_empty_for_nonexistent_pid(leerie):
    """Sanity: a sentinel PID with no children returns empty set."""
    assert leerie._enumerate_descendants(999_999_999) == set()


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only test.",
)
def test_descendant_tracker_is_safe_on_nonexistent_pid(leerie):
    """`_DescendantTracker(sentinel_pid).stop_and_reap()` must not raise —
    used in `_invoke`'s success path and must be idempotent even when the
    leader has no descendants at all."""
    import asyncio

    async def _run():
        tracker = leerie._DescendantTracker(999_999_999)
        tracker.start()
        await asyncio.sleep(0.1)  # one poll cycle
        leaked = await tracker.stop_and_reap()
        assert leaked == 0
        # Idempotent
        leaked2 = await tracker.stop_and_reap()
        assert leaked2 == 0

    asyncio.run(_run())


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX-only test.",
)
def test_descendant_tracker_records_descendants_during_lifetime(leerie):
    """Verify the tracker actually accumulates PIDs across multiple poll
    cycles, not just at start or stop. This guards against a regression
    where the poll loop is broken (e.g. caught CancelledError too eagerly)
    and only sees the descendant set at one moment."""
    import asyncio
    import subprocess

    async def _run():
        leader = await asyncio.create_subprocess_exec(
            "bash", "-c", "sleep 18181 & wait",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        tracker = leerie._DescendantTracker(leader.pid)
        tracker.start()
        # Wait for at least 2 poll cycles to catch the sleep
        await asyncio.sleep(1.5)
        # Snapshot what tracker has accumulated
        accumulated = set(tracker._seen)
        # Clean up
        leader.kill()
        await leader.wait()
        leaked = await tracker.stop_and_reap()
        subprocess.run(["pkill", "-9", "-f", "sleep 18181"], capture_output=True)

        assert len(accumulated) >= 1, (
            f"tracker accumulated 0 descendants during its polling lifetime "
            f"(expected at least 1 — the sleep was alive for 1.5s and the "
            f"poll interval is 0.5s)"
        )

    asyncio.run(_run())


# --- _reparented_orphans unit tests ----------------------------------------

def test_reparented_orphans_filters_by_ppid_and_age(leerie, monkeypatch):
    """`_reparented_orphans` must return only PIDs that are in `seen`,
    have ppid==1, and have etimes >= _PID_REAP_MIN_AGE_SEC."""
    min_age = leerie._PID_REAP_MIN_AGE_SEC

    # Fake ps output: pid 100 (ppid=1, old), pid 101 (ppid=2, old — attached),
    # pid 102 (ppid=1, young), pid 103 (ppid=1, old — NOT in seen)
    fake_ps = (
        "  PID  PPID ELAPSED\n"
        f"  100     1 {min_age + 100}\n"
        f"  101     2 {min_age + 50}\n"
        f"  102     1 {min_age - 1}\n"
        f"  103     1 {min_age + 200}\n"
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    seen = {100, 101, 102}
    result = leerie._reparented_orphans(seen)
    assert result == [100], f"expected [100], got {result}"


def test_reparented_orphans_sorted_oldest_first(leerie, monkeypatch):
    """Older PIDs must appear first in the returned list."""
    min_age = leerie._PID_REAP_MIN_AGE_SEC

    fake_ps = (
        "  PID  PPID ELAPSED\n"
        f"  200     1 {min_age + 10}\n"
        f"  201     1 {min_age + 50}\n"
        f"  202     1 {min_age + 30}\n"
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    seen = {200, 201, 202}
    result = leerie._reparented_orphans(seen)
    # Oldest-first: 201 (50), 202 (30), 200 (10)
    assert result == [201, 202, 200], f"expected oldest-first order, got {result}"


def test_reparented_orphans_empty_on_ps_failure(leerie, monkeypatch):
    """Must return [] if ps fails — same empty-set fallback as
    _enumerate_descendants."""
    import subprocess as sp

    def fake_run(cmd, **kwargs):
        raise sp.SubprocessError("ps failed")

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    result = leerie._reparented_orphans({1, 2, 3})
    assert result == []


def test_reparented_orphans_empty_set_input(leerie, monkeypatch):
    """Empty seen set always returns []."""
    fake_ps = "  PID  PPID ELAPSED\n  100     1   999\n"

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    result = leerie._reparented_orphans(set())
    assert result == []


# --- _poll_loop pressure-gated reaping tests -------------------------------

def test_poll_loop_no_reaping_without_cgroup_sid(leerie, monkeypatch):
    """When cgroup_sid is None, _poll_loop must never call _cgroup_stat
    or kill anything — byte-identical to the pre-reaping behavior."""
    import asyncio

    stat_calls: list = []
    kill_calls: list = []

    monkeypatch.setattr(leerie, "_cgroup_stat",
                        lambda sid: stat_calls.append(sid) or None)
    monkeypatch.setattr(leerie, "_enumerate_descendants",
                        lambda pid: set())

    def fake_signal_pids(pids, s):
        kill_calls.extend(pids)

    monkeypatch.setattr(leerie, "_signal_pids", fake_signal_pids)

    async def _run():
        tracker = leerie._DescendantTracker(99999, cgroup_sid=None)
        tracker.start()
        await asyncio.sleep(leerie._DESCENDANT_POLL_SEC * 3)
        await tracker.stop_and_reap()

    asyncio.run(_run())
    assert stat_calls == [], (
        f"_cgroup_stat must not be called without cgroup_sid; got {stat_calls}")
    assert kill_calls == [], (
        f"No PIDs should be killed without cgroup_sid; got {kill_calls}")


def test_poll_loop_reaps_above_high_water(leerie, monkeypatch):
    """_poll_loop must reap orphans when pressure >= high-water and stop
    killing once pressure drops below low-water."""
    import asyncio

    killed: list[int] = []
    min_age = leerie._PID_REAP_MIN_AGE_SEC
    fake_ps_out = (
        "  PID  PPID ELAPSED\n"
        f"  500     1 {min_age + 100}\n"
        f"  501     1 {min_age + 50}\n"
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps_out
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    monkeypatch.setattr(leerie, "_enumerate_descendants",
                        lambda pid: {500, 501})

    call_count = [0]

    def fake_cgroup_stat(sid):
        call_count[0] += 1
        if call_count[0] == 1:
            return (92, 100, 0)   # armed: 92% >= 90%
        if call_count[0] == 2:
            return (91, 100, 0)   # still above low-water after 1 kill
        return (70, 100, 0)       # below low-water: stop

    monkeypatch.setattr(leerie, "_cgroup_stat", fake_cgroup_stat)
    monkeypatch.setattr(leerie, "_signal_pids",
                        lambda pids, s: killed.extend(pids))

    async def _run():
        tracker = leerie._DescendantTracker(99999, cgroup_sid="test-cgroup")
        tracker._seen = {500, 501}
        tracker.start()
        await asyncio.sleep(leerie._DESCENDANT_POLL_SEC * 2)
        await tracker.stop_and_reap()

    asyncio.run(_run())
    assert len(killed) >= 1, f"Expected at least one kill above high-water; got {killed}"


def test_poll_loop_below_high_water_kills_nothing(leerie, monkeypatch):
    """When pressure is below high-water, zero mid-run kills regardless
    of how many old orphans are present in _seen."""
    import asyncio

    mid_run_killed: list[int] = []
    min_age = leerie._PID_REAP_MIN_AGE_SEC
    fake_ps_out = (
        "  PID  PPID ELAPSED\n"
        + "".join(f"  {p}     1 {min_age + 200}\n" for p in range(400, 450))
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps_out
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    monkeypatch.setattr(leerie, "_enumerate_descendants",
                        lambda pid: set(range(400, 450)))
    # 58% — below 90% high-water
    monkeypatch.setattr(leerie, "_cgroup_stat",
                        lambda sid: (58, 100, 0))

    def track_kills(pids, s):
        mid_run_killed.extend(pids)

    monkeypatch.setattr(leerie, "_signal_pids", track_kills)

    async def _run():
        tracker = leerie._DescendantTracker(99999, cgroup_sid="cg-below")
        tracker._seen = set(range(400, 450))
        tracker.start()
        await asyncio.sleep(leerie._DESCENDANT_POLL_SEC * 2)
        # Stop without calling stop_and_reap (which would SIGKILL _seen at exit)
        tracker._stopped = True
        if tracker._task:
            tracker._task.cancel()
            tracker._task = None

    asyncio.run(_run())
    assert mid_run_killed == [], (
        f"Expected zero mid-run kills below high-water; got {mid_run_killed}")


def test_poll_loop_young_orphan_not_reaped(leerie, monkeypatch):
    """A reparented PID younger than _PID_REAP_MIN_AGE_SEC must never be
    reaped — even when pressure is above high-water."""
    import asyncio

    killed: list[int] = []
    min_age = leerie._PID_REAP_MIN_AGE_SEC

    fake_ps_out = (
        "  PID  PPID ELAPSED\n"
        f"  700     1 {min_age - 1}\n"  # too young
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps_out
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    monkeypatch.setattr(leerie, "_enumerate_descendants",
                        lambda pid: {700})
    monkeypatch.setattr(leerie, "_cgroup_stat",
                        lambda sid: (95, 100, 0))  # above high-water
    monkeypatch.setattr(leerie, "_signal_pids",
                        lambda pids, s: killed.extend(pids))

    async def _run():
        tracker = leerie._DescendantTracker(99999, cgroup_sid="cg-young")
        tracker._seen = {700}
        tracker.start()
        await asyncio.sleep(leerie._DESCENDANT_POLL_SEC * 2)
        tracker._stopped = True
        if tracker._task:
            tracker._task.cancel()
            tracker._task = None

    asyncio.run(_run())
    assert 700 not in killed, f"Young PID 700 must not be reaped; killed={killed}"


def test_poll_loop_attached_pid_not_reaped(leerie, monkeypatch):
    """A PID with ppid != 1 (still attached to the worker tree) must not
    be reaped — only reparented orphans (ppid==1) are eligible."""
    import asyncio

    killed: list[int] = []
    min_age = leerie._PID_REAP_MIN_AGE_SEC

    # PID 800 is old but ppid=1234 (attached)
    fake_ps_out = (
        "  PID  PPID ELAPSED\n"
        f"  800  1234 {min_age + 200}\n"
    )

    def fake_run(cmd, **kwargs):
        class R:
            stdout = fake_ps_out
        return R()

    monkeypatch.setattr(leerie.subprocess, "run", fake_run)
    monkeypatch.setattr(leerie, "_enumerate_descendants",
                        lambda pid: {800})
    monkeypatch.setattr(leerie, "_cgroup_stat",
                        lambda sid: (95, 100, 0))  # above high-water
    monkeypatch.setattr(leerie, "_signal_pids",
                        lambda pids, s: killed.extend(pids))

    async def _run():
        tracker = leerie._DescendantTracker(99999, cgroup_sid="cg-attached")
        tracker._seen = {800}
        tracker.start()
        await asyncio.sleep(leerie._DESCENDANT_POLL_SEC * 2)
        tracker._stopped = True
        if tracker._task:
            tracker._task.cancel()
            tracker._task = None

    asyncio.run(_run())
    assert 800 not in killed, f"Attached PID 800 must not be reaped; killed={killed}"


# --- cgroup_sid=None default — constructor compatibility guard ----------------

def test_descendant_tracker_cgroup_sid_defaults_to_none():
    """Static: `_DescendantTracker.__init__` must declare `cgroup_sid` with
    a `= None` default so the 3 pre-existing direct `_DescendantTracker(proc.pid)`
    constructor call sites in this test file keep working without changes.
    Pin this structurally — a refactor that drops the default breaks all three."""
    leerie_src = LEERIE_PY.read_text()
    m = re.search(
        r"class _DescendantTracker:.*?def __init__\(self[^)]+\):",
        leerie_src, re.DOTALL,
    )
    assert m, "could not locate _DescendantTracker.__init__ in leerie.py"
    init_sig = m.group(0)
    assert "cgroup_sid: str | None = None" in init_sig, (
        "_DescendantTracker.__init__ must declare `cgroup_sid: str | None = None` "
        "so the 3 pre-existing bare _DescendantTracker(proc.pid) constructors "
        "in this test file continue to work without any changes. "
        "A refactor that removes the default or changes the type annotation "
        "breaks backward compatibility with every existing call site."
    )
