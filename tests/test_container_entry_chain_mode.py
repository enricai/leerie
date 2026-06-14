"""Tests for the chain-mode branch in scripts/container-entry.sh.

Chain workers are launched by the per-chain coordinator via the bare
Fly Machines API (chain.fly_client.launch_machine) with no init.cmd or
init.exec, so they boot with the worker image's ENTRYPOINT
(container-entry.sh) and no argv. Without a chain-aware branch the
script would fall through to ``sleep infinity`` and the orchestrator
would never start.

These tests run container-entry.sh inside a subprocess with stubs for
``runuser``, ``install``, ``base64``, ``chown``, and ``getent`` so the
script's privilege drops, credential materialization, and
orchestrator/heartbeat invocations are recordable rather than actually
executed.

The chain-mode branch is triggered by setting LEERIE_CHAIN_ID +
LEERIE_CHAIN_RUN_UUID + LEERIE_TASK + LEERIE_CLAUDE_CREDS_B64 in the
environment, with no positional args.
"""
from __future__ import annotations

import base64
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"

# Minimum chain-mode env: every test that wants to reach the chain
# branch sets these (plus whatever the specific test is asserting).
# Centralized so future env-name renames touch one place.
_MIN_CHAIN_ENV: dict[str, str] = {
    "LEERIE_CHAIN_ID": "test-chain-uuid",
    "LEERIE_CHAIN_RUN_UUID": "test-chain-run-uuid",
    "LEERIE_TASK": "do the thing",
    "LEERIE_CLAUDE_CREDS_B64": base64.b64encode(b'{"claudeAiOauth":{"accessToken":"sk-stub"}}').decode(),
    "LEERIE_COORDINATOR_HOST": "coord.vm.leerie.internal:8080",
}


# ---------------------------------------------------------------------------
# Stub bin
# ---------------------------------------------------------------------------

def _make_stubs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Stand up a stub-bin directory on PATH that captures the script's
    side-effects without actually privilege-dropping or shelling out.

    Returns ``(stub_bin_dir, exec_log_path, heartbeat_log_path)``.
    """
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    exec_log = tmp_path / "exec.log"
    heartbeat_log = tmp_path / "heartbeat.log"
    exec_log.write_text("")
    heartbeat_log.write_text("")

    # `runuser` stub: distinguishes heartbeat-launch from orchestrator-run
    # by argv. The new chain branch does NOT exec the orchestrator (it
    # runs + captures rc + invokes the chain-exit-hook), so both runuser
    # callsites must return cleanly.
    runuser = bin_dir / "runuser"
    runuser.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        echo "runuser $@" >> "{exec_log}"
        for a in "$@"; do
          case "$a" in
            *heartbeat.sh)
              echo "heartbeat-launched" >> "{heartbeat_log}"
              exit 0
              ;;
            *leerie.py)
              echo "orchestrator-ran" >> "{exec_log}"
              # Return non-zero or zero is the test's choice — default 0.
              exit "${{LEERIE_STUB_ORCH_RC:-0}}"
              ;;
          esac
        done
        exit 0
        """))
    runuser.chmod(0o755)

    # `bash` invocations from the chain-exit-hook subshell: capture and exit 0.
    # The chain-mode branch runs `bash -c '... leerie_chain_report ...'`.
    # We can't stub bash itself (the wrapper script needs it), so we
    # stub the chain-exit-hook via a writable path — see _run_entry's
    # hook overlay.

    # python3 stub: never reached if runuser intercepts, but safety net.
    py = bin_dir / "python3"
    py.write_text(f"#!/bin/sh\necho \"python3 $@\" >> \"{exec_log}\"\nexit 0\n")
    py.chmod(0o755)

    # getent / chown: best-effort identity ops.
    for name in ("getent", "chown"):
        p = bin_dir / name
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)

    # `install -d ...` is used to mkdir + chmod the .claude home. The
    # real `install` errors when -o leerie -g leerie can't resolve a
    # user named "leerie" on the test host. Stub it to a plain mkdir.
    install_stub = bin_dir / "install"
    install_stub.write_text(textwrap.dedent("""\
        #!/bin/sh
        # Skip flags until we see a path-shaped arg.
        for a in "$@"; do
          case "$a" in
            -*|leerie|leerie:leerie) ;;
            *) [ -n "$a" ] && mkdir -p "$a" 2>/dev/null || true ;;
          esac
        done
        exit 0
        """))
    install_stub.chmod(0o755)

    return bin_dir, exec_log, heartbeat_log


def _run_entry(
    tmp_path: Path,
    env: dict[str, str],
    args: list[str] | None = None,
) -> subprocess.CompletedProcess:
    bin_dir, exec_log, heartbeat_log = _make_stubs(tmp_path)
    base_env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path),
    }
    base_env.update(env)

    # The script does `cd /work` and writes `/home/leerie/.claude/...`.
    # We can't write to those paths on a test host. Wrap container-entry
    # in a shell function that intercepts `cd /work`, redirects writes
    # to a tmp HOME, and stubs `command -v` results as needed.
    fake_work = tmp_path / "fake-work"
    fake_work.mkdir(exist_ok=True)
    fake_home_leerie = tmp_path / "fake-home-leerie"
    fake_home_leerie.mkdir(exist_ok=True)

    # Hook overlay: when the script sources the chain-exit-hook, we want
    # to record `leerie_chain_report` was called with the right rc and
    # run_dir without making a real curl POST. Provide a fake hook +
    # _log.sh on the path the script reads from.
    fake_image = tmp_path / "fake-image"
    fake_image_scripts = fake_image / "scripts"
    fake_image_scripts.mkdir(parents=True)
    (fake_image_scripts / "remote").mkdir()
    (fake_image_scripts / "remote" / "_log.sh").write_text(
        "remote_log() { printf '[remote_log] %s\\n' \"$*\" >&2; }\n"
    )
    hook_log = tmp_path / "hook.log"
    (fake_image_scripts / "leerie-chain-exit-hook.sh").write_text(textwrap.dedent(f"""\
        # Fake chain-exit-hook for tests.
        leerie_chain_report() {{
          local rc="$1"
          local run_dir="$2"
          printf 'leerie_chain_report rc=%s run_dir=%s\\n' "$rc" "$run_dir" >> "{hook_log}"
          return 0
        }}
        """))

    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        # Redirect /work and /home/leerie to test-local paths.
        cd() {{
          if [ "$1" = "/work" ]; then
            builtin cd "{fake_work}" 2>/dev/null || command cd "{fake_work}"
            return $?
          fi
          command cd "$@"
        }}
        # Replace the /opt/leerie-image path with our overlay so the
        # chain-mode branch sources our fake hook + _log.sh.
        sed -e 's:/opt/leerie-image:{fake_image}:g' \\
            -e 's:/home/leerie:{fake_home_leerie}:g' \\
            "{ENTRY}" > "{tmp_path}/entry-rewritten.sh"
        . "{tmp_path}/entry-rewritten.sh"
        """))
    wrapper.chmod(0o755)

    cmd = ["bash", str(wrapper)] + (args or [])
    result = subprocess.run(
        cmd, env=base_env, capture_output=True, text=True, timeout=10,
    )
    result.exec_log = exec_log.read_text() if exec_log.exists() else ""
    result.heartbeat_log = heartbeat_log.read_text() if heartbeat_log.exists() else ""
    result.hook_log = hook_log.read_text() if hook_log.exists() else ""
    result.creds_path = fake_home_leerie / ".claude" / ".credentials.json"
    return result


# ---------------------------------------------------------------------------
# Chain-mode happy path
# ---------------------------------------------------------------------------

def test_chain_mode_materializes_creds_starts_heartbeat_and_reports(tmp_path: Path) -> None:
    """Full chain-mode happy path:

    - Decodes LEERIE_CLAUDE_CREDS_B64 to ~/.claude/.credentials.json.
    - Background-starts the heartbeat.
    - Runs (not exec's) the orchestrator with no --run-id.
    - Invokes leerie_chain_report with the orchestrator's exit rc.
    """
    result = _run_entry(tmp_path, dict(_MIN_CHAIN_ENV))
    assert result.returncode == 0, result.stderr
    # Heartbeat backgrounded.
    assert "heartbeat-launched" in result.heartbeat_log
    # Orchestrator ran (not exec'd).
    assert "orchestrator-ran" in result.exec_log
    # No --run-id flag passed (M1).
    assert "--run-id" not in result.exec_log
    # Chain-exit-hook fired with rc 0.
    assert "leerie_chain_report rc=0" in result.hook_log
    # Credentials written and decoded correctly.
    assert result.creds_path.exists()
    assert result.creds_path.read_text() == '{"claudeAiOauth":{"accessToken":"sk-stub"}}'


def test_chain_mode_propagates_orchestrator_rc(tmp_path: Path) -> None:
    """A non-zero orchestrator rc flows through to container-entry's exit
    code AND to leerie_chain_report's first arg."""
    env = dict(_MIN_CHAIN_ENV)
    env["LEERIE_STUB_ORCH_RC"] = "11"
    result = _run_entry(tmp_path, env)
    assert result.returncode == 11
    assert "leerie_chain_report rc=11" in result.hook_log


# ---------------------------------------------------------------------------
# Chain-mode required-env guards
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("missing_var,expected_in_stderr", [
    ("LEERIE_TASK", "LEERIE_TASK"),
    ("LEERIE_CHAIN_RUN_UUID", "LEERIE_CHAIN_RUN_UUID"),
    ("LEERIE_CLAUDE_CREDS_B64", "LEERIE_CLAUDE_CREDS_B64"),
])
def test_chain_mode_required_env_missing_errors_with_code_64(
    tmp_path: Path, missing_var: str, expected_in_stderr: str
) -> None:
    """Each chain-mode required env var, when missing, fails with rc 64
    and a diagnostic naming the variable."""
    env = dict(_MIN_CHAIN_ENV)
    del env[missing_var]
    result = _run_entry(tmp_path, env)
    assert result.returncode == 64
    assert expected_in_stderr in result.stderr


# ---------------------------------------------------------------------------
# Non-chain fallback
# ---------------------------------------------------------------------------

def test_non_chain_mode_does_not_start_heartbeat(tmp_path: Path) -> None:
    """No LEERIE_CHAIN_ID → falls through to the Fly idle path.
    Heartbeat, orchestrator-run, and the hook MUST NOT fire."""
    result = _run_entry(tmp_path, {})
    assert result.returncode == 0
    assert result.heartbeat_log == ""
    assert "orchestrator-ran" not in result.exec_log
    assert result.hook_log == ""


def test_non_chain_mode_with_argv_does_not_trigger_chain_branch(tmp_path: Path) -> None:
    """Chain-mode is gated on $# == 0. Local nerdctl always passes argv,
    so LEERIE_CHAIN_ID + positional args routes to the existing local
    code path — not the chain branch."""
    env = {"LEERIE_CHAIN_ID": "should-not-trigger"}
    result = _run_entry(tmp_path, env, args=["arg1", "arg2"])
    assert result.returncode == 0
    assert result.heartbeat_log == ""
    assert result.hook_log == ""
