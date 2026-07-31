"""Tests for scripts/remote/provision.sh.

provision.sh is sourced (not exec'd) by the leerie launcher's REMOTE=true
branch.  These tests exercise the script's bash logic in isolation via
subprocess, with flyctl stubbed out so no real Fly.io calls are made.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"


def _run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    # Exclude LEERIE_STATE_DIR, LEERIE_STATE_HOST_DIR, and XDG_CACHE_HOME from
    # the base environment so tests run in isolation. XDG_CACHE_HOME points to
    # /tmp/.cache which may not be writable in all test contexts, causing mkdir
    # failures in lib.sh's _leerie_fly_agent_ensure function.
    base_env = {k: v for k, v in os.environ.items()
                if k not in ("LEERIE_STATE_DIR", "LEERIE_STATE_HOST_DIR", "XDG_CACHE_HOME")}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def test_provision_sh_exists():
    assert PROVISION_SH.exists(), "scripts/remote/provision.sh is missing"


def test_provision_sh_is_executable():
    assert os.access(PROVISION_SH, os.X_OK), (
        "scripts/remote/provision.sh is not executable"
    )


def test_provision_machine_fails_without_fly_image_tag():
    """provision_machine returns 1 when FLY_IMAGE_TAG is unset."""
    result = _run_bash(
        f"source {PROVISION_SH}; provision_machine",
        env={"FLY_IMAGE_TAG": ""},
    )
    assert result.returncode != 0
    assert "FLY_IMAGE_TAG" in result.stderr


def test_provision_machine_fails_when_flyctl_missing():
    """provision_machine returns 1 with an actionable error when flyctl is absent."""
    # Override PATH to a directory with no flyctl binary.
    result = _run_bash(
        f"source {PROVISION_SH}; provision_machine",
        env={
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
            "PATH": "/usr/bin:/bin",  # no flyctl here
        },
    )
    assert result.returncode != 0
    assert "flyctl" in result.stderr.lower()


def test_destroy_machine_noop_when_no_machine_id():
    """destroy_machine is idempotent: returns 0 when LEERIE_MACHINE_ID is empty."""
    result = _run_bash(
        f"source {PROVISION_SH}; LEERIE_MACHINE_ID=''; destroy_machine; echo 'ok'",
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_provision_machine_fails_when_flyctl_unauthenticated(tmp_path):
    """provision_machine returns 1 with auth error when flyctl is present but not authed."""
    # Create a stub flyctl that is present on PATH but reports unauthenticated.
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text("#!/usr/bin/env bash\n"
                           "if [ \"$1\" = 'auth' ] && [ \"$2\" = 'status' ]; then\n"
                           "  exit 1\n"
                           "fi\n"
                           "exit 0\n")
    fake_flyctl.chmod(0o755)

    result = _run_bash(
        f"source {PROVISION_SH}; provision_machine",
        env={
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode != 0
    assert "authenticated" in result.stderr.lower() or "auth" in result.stderr.lower()


def test_provision_machine_exports_machine_id_on_success(tmp_path):
    """provision_machine exports LEERIE_MACHINE_ID on successful create + started."""
    # Stub flyctl: auth succeeds, machine run returns text containing
    # "Machine ID: <id>" (the real flyctl format — `flyctl machine run`
    # does NOT support --json), status returns JSON started.
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'auth' ] && [ \"$2\" = 'status' ]; then\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = 'machine' ] && [ \"$2\" = 'run' ]; then\n"
        "  printf 'Success! A Machine has been launched\\n Machine ID: test-machine-001\\n State: created\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = 'machine' ] && [ \"$2\" = 'status' ]; then\n"
        "  printf 'Machine ID: test-machine\\nState: started\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    result = _run_bash(
        f"source {PROVISION_SH}; provision_machine && echo \"machine=$LEERIE_MACHINE_ID\"",
        env={
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "machine=test-machine-001" in result.stdout


def test_destroy_machine_called_on_exit_trap(tmp_path):
    """The EXIT trap fires destroy_machine so no machine leaks on clean exit."""
    fake_flyctl = tmp_path / "flyctl"
    fake_flyctl.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = 'auth' ] && [ \"$2\" = 'status' ]; then exit 0; fi\n"
        "if [ \"$1\" = 'machine' ] && [ \"$2\" = 'run' ]; then\n"
        "  printf 'Success!\\n Machine ID: trap-test-machine\\n State: created\\n'\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"$1\" = 'machine' ] && [ \"$2\" = 'status' ]; then\n"
        "  printf 'Machine ID: trap-test-machine\\nState: started\\n'; exit 0\n"
        "fi\n"
        "if [ \"$1\" = 'machine' ] && [ \"$2\" = 'destroy' ]; then\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_flyctl.chmod(0o755)

    # Run in a subshell so the EXIT trap fires when it exits.
    # Override _try_fetch_branch_for_teardown *after* sourcing provision.sh
    # so decide_teardown takes the sync-success branch and reaches
    # destroy_machine. The sync-failure branch (which leaves the machine
    # running by design) is not what this test is verifying — see
    # scripts/remote/provision.sh:176.
    result = _run_bash(
        f"( source {PROVISION_SH}; "
        "_try_fetch_branch_for_teardown() { return 0; }; "
        "provision_machine )",
        env={
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
            "PATH": f"{tmp_path}:/usr/bin:/bin",
        },
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The destroy trap fired: provision.sh logs "destroying machine <id>" on stderr.
    assert "destroying machine trap-test-machine" in result.stderr, (
        "destroy_machine was not called via EXIT trap — machine would leak.\n"
        f"stderr: {result.stderr}"
    )
