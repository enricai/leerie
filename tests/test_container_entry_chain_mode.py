"""Tests for the chain-mode branch in scripts/container-entry.sh.

Chain workers are launched by the per-chain coordinator via the bare
Fly Machines API (chain.fly_client.launch_machine) with no init.cmd or
init.exec, so they boot with the worker image's ENTRYPOINT
(container-entry.sh) and no argv. Without a chain-aware branch the
script would fall through to ``sleep infinity`` and the orchestrator
would never start — see v2 audit S1.

These tests run container-entry.sh inside a subprocess with ``runuser``
stubbed out (to capture what would have been exec'd) and ``bash``
stubbed (to capture the heartbeat-script invocation). The chain-mode
branch is triggered by setting LEERIE_CHAIN_ID + LEERIE_RUN_ID +
LEERIE_TASK in the environment, with no positional args.
"""
from __future__ import annotations

import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Write stub binaries on $PATH so container-entry.sh's privilege drops
    and orchestrator/heartbeat invocations are recordable rather than
    actually executed.

    Returns ``(stub_bin_dir, exec_log_path, heartbeat_log_path)``.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    exec_log = tmp_path / "exec.log"
    heartbeat_log = tmp_path / "heartbeat.log"
    exec_log.write_text("")
    heartbeat_log.write_text("")

    # `runuser` stub: records the full exec'd command line plus selected
    # env vars and either exits 0 (background-start case) or pretends to
    # exec the orchestrator. Two distinct uses of runuser inside the
    # chain-mode branch:
    #   1. Background heartbeat launch (`runuser -u leerie -- env ... bash heartbeat.sh`)
    #   2. exec'd orchestrator (`runuser -u leerie -- env ... python3 leerie.py ...`)
    # We tell them apart by which executable name appears in argv after
    # `env ...`.
    runuser = bin_dir / "runuser"
    runuser.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            echo "$@" >> "{exec_log}"
            # Inspect args to determine which invocation this is. The
            # heartbeat launch ends with /bin/bash ... heartbeat.sh; the
            # orchestrator launch ends with python3 leerie.py ...
            for a in "$@"; do
              case "$a" in
                *heartbeat.sh)
                  # Heartbeat: log + exit 0 (parent script continues to
                  # the exec orchestrator step).
                  echo "heartbeat-launched" >> "{heartbeat_log}"
                  exit 0
                  ;;
                *leerie.py)
                  # Orchestrator: record the runuser invocation. The
                  # parent's `exec` semantics mean we don't return; we
                  # just exit 0 here so the test sees a clean script
                  # exit.
                  echo "orchestrator-exec" >> "{exec_log}"
                  exit 0
                  ;;
              esac
            done
            # Default (sleep infinity branch): also clean exit so tests
            # don't hang.
            exit 0
            """
        )
    )
    runuser.chmod(0o755)

    # `python3` stub: never actually called because runuser intercepts;
    # provided for safety so nothing tries to launch the real
    # orchestrator.
    py = bin_dir / "python3"
    py.write_text("#!/bin/sh\necho python3-stub: $@ >> " + str(exec_log) + "\nexit 0\n")
    py.chmod(0o755)

    # `getent` stub: container-entry.sh runs `getent passwd leerie` for
    # the chown /work step. Treat as success so the script reaches the
    # chain-mode branch on a host that doesn't have a `leerie` user.
    getent = bin_dir / "getent"
    getent.write_text("#!/bin/sh\nexit 0\n")
    getent.chmod(0o755)

    # `chown` stub: chain-mode branch's parent code runs chown best-effort
    # against /sys/fs/cgroup and /work; let those silently no-op.
    chown = bin_dir / "chown"
    chown.write_text("#!/bin/sh\nexit 0\n")
    chown.chmod(0o755)

    return bin_dir, exec_log, heartbeat_log


def _run_entry(
    tmp_path: Path,
    env: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    bin_dir, exec_log, heartbeat_log = _make_stubs(tmp_path)
    base_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        # /work needs to exist for `cd /work`. Stand up a fake.
        "HOME": str(tmp_path),
    }
    base_env.update(env)
    # cd /work happens in the script; we monkey around by making /work
    # writable via a tmpdir cwd.
    fake_work = tmp_path / "fake-work"
    fake_work.mkdir(exist_ok=True)
    # The script uses `cd /work` literally — we can't redirect that
    # without invasive changes. Instead we rely on `cd /work || true`
    # behavior: if /work doesn't exist as a directory the script with
    # `set -e` would exit, which is fine — we then can't test chain-mode.
    # On macOS/Linux dev machines /work usually doesn't exist; we work
    # around by running the script with `set +e` injected via PATH.
    # Simpler: just override `cd` via a shell function in a wrapper script.
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            cd() {{
              # Allow `cd /work` to silently succeed regardless of host fs.
              if [ "$1" = "/work" ]; then
                builtin cd "{fake_work}" 2>/dev/null || command cd "{fake_work}"
                return $?
              fi
              command cd "$@"
            }}
            . {ENTRY}
            """
        )
    )
    wrapper.chmod(0o755)

    cmd = ["bash", str(wrapper)] + (args or [])
    result = subprocess.run(
        cmd, env=base_env, capture_output=True, text=True, timeout=10,
    )
    result.exec_log = exec_log.read_text() if exec_log.exists() else ""
    result.heartbeat_log = heartbeat_log.read_text() if heartbeat_log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# Chain-mode entry: happy path
# ---------------------------------------------------------------------------


def test_chain_mode_starts_heartbeat_and_exec_orchestrator(tmp_path: Path) -> None:
    """LEERIE_CHAIN_ID + LEERIE_RUN_ID + LEERIE_TASK set, no argv:
    container-entry.sh background-starts the heartbeat then exec's the
    orchestrator with --run-id $LEERIE_RUN_ID + the task as positional.
    """
    env = {
        "LEERIE_CHAIN_ID": "test-chain-uuid-001",
        "LEERIE_RUN_ID": "test-run-id-001",
        "LEERIE_TASK": "do the thing",
        "LEERIE_COORDINATOR_HOST": "coord.vm.leerie.internal:8080",
    }
    result = _run_entry(tmp_path, env)
    assert result.returncode == 0, result.stderr
    # Heartbeat backgrounded.
    assert "heartbeat-launched" in result.heartbeat_log
    # Orchestrator exec'd with the right args.
    assert "orchestrator-exec" in result.exec_log
    assert "--run-id test-run-id-001" in result.exec_log
    assert "do the thing" in result.exec_log


def test_chain_mode_missing_task_errors_with_code_64(tmp_path: Path) -> None:
    """LEERIE_CHAIN_ID set but LEERIE_TASK empty → exit 64 + diagnostic."""
    env = {
        "LEERIE_CHAIN_ID": "test-chain-uuid-002",
        "LEERIE_RUN_ID": "test-run-id-002",
        # No LEERIE_TASK.
    }
    result = _run_entry(tmp_path, env)
    assert result.returncode == 64
    assert "LEERIE_TASK" in result.stderr


def test_chain_mode_missing_run_id_errors_with_code_64(tmp_path: Path) -> None:
    """LEERIE_CHAIN_ID set but LEERIE_RUN_ID empty → exit 64 + diagnostic."""
    env = {
        "LEERIE_CHAIN_ID": "test-chain-uuid-003",
        "LEERIE_TASK": "something",
    }
    result = _run_entry(tmp_path, env)
    assert result.returncode == 64
    assert "LEERIE_RUN_ID" in result.stderr


def test_non_chain_mode_does_not_start_heartbeat(tmp_path: Path) -> None:
    """No LEERIE_CHAIN_ID + no argv → falls through to the existing
    Fly path (`sleep infinity`). The heartbeat MUST NOT be started.
    """
    # No chain env vars at all.
    result = _run_entry(tmp_path, {})
    assert result.returncode == 0
    assert result.heartbeat_log == ""
    # And the chain-mode orchestrator exec didn't fire.
    assert "orchestrator-exec" not in result.exec_log


def test_non_chain_mode_with_argv_runs_orchestrator(tmp_path: Path) -> None:
    """Local nerdctl path: $# > 0 means the launcher passed argv directly.
    Chain-mode branch is gated on $# == 0 AND LEERIE_CHAIN_ID, so even with
    LEERIE_CHAIN_ID set, positional args route to the existing local code
    path (not the chain branch).
    """
    env = {"LEERIE_CHAIN_ID": "should-not-trigger"}
    result = _run_entry(tmp_path, env, args=["arg1", "arg2"])
    assert result.returncode == 0
    assert result.heartbeat_log == ""
