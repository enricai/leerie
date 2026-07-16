"""Tests for scripts/remote/ec2-ssm.sh (`ec2_launch_detached` / `ec2_attach`).

ec2-ssm.sh is the SSM Session Manager transport substitution for `flyctl
ssh console`'s *launch* and *attach* roles (DESIGN §6 "Transport
substitution for `flyctl ssh console`"). `aws ssm start-session
--target <id> --document-name AWS-StartInteractiveCommand --parameters
command="python3 -"` (or `command="sh -s"` for attach) is the SSM analog
of `flyctl ssh console --pty=false -C "python3 -"` — a short bootstrap
command naming the interpreter, with the real (multi-KB) payload piped
through as stdin, which the session-manager-plugin's interactive session
forwards to the remote interpreter exactly like a normal ssh session
would. This is distinct from ec2_remote_exec (ec2-lib.sh), which embeds
its *entire* wrapped command inside the `--parameters command=[...]`
document parameter — fine for ec2_remote_exec's short probe/mkdir/chown
commands, but that parameter has a ~4 KB ceiling, far under the size of
a real launch-wrapper or tail-wrapper script.

Exercised via subprocess against a stubbed `aws` binary that models
`ssm start-session`'s two defining quirks: (1) it always exits 0 itself
regardless of the wrapped remote command's real exit status (the
documented session-manager-plugin limitation both ec2_remote_exec and
this file work around via an rc-sentinel), and (2) it is a genuinely
interactive session, i.e. it drains its own stdin and executes it as the
bootstrap interpreter's program — unlike test_ec2_transport.py's
`_stub_aws_ssm`, which only ever inspects the `--parameters` value and
never touches stdin (ec2_remote_exec never pipes anything to it). No
real AWS calls are made.
"""
from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
EC2_SSM_SH = REPO_ROOT / "scripts" / "remote" / "ec2-ssm.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _stub_timeout(bin_dir: Path) -> None:
    """Real, group-killing `timeout` stub — see test_ec2_transport.py's
    identical helper for the full rationale (macOS ships no
    /usr/bin/timeout, so an unbounded stall test would hang the suite)."""
    stub = bin_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
kill_after=""
while [[ "$1" == --* ]]; do
  case "$1" in
    --kill-after=*) kill_after="${1#--kill-after=}" ;;
    --kill-after)   kill_after="$2"; shift ;;
    --foreground)   ;;
  esac
  shift
done
secs="$1"; shift

set -m
"$@" &
child=$!
set +m

(
  sleep "$secs"
  kill -TERM -- "-$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null
  if [ -n "$kill_after" ]; then
    sleep "$kill_after"
    kill -KILL -- "-$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null
  fi
) &
waiter=$!

wait "$child" 2>/dev/null; rc=$?
kill -TERM "$waiter" 2>/dev/null
[ "$rc" -eq 143 ] && rc=124
exit "$rc"
"""
    )
    stub.chmod(0o755)


def _run_bash(script: str, env: dict, *, input: str | None = None,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        input=input,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


def _stub_aws_interactive_session(bin_dir: Path, *, stall: bool = False) -> Path:
    """Write a stub `aws` binary modeling `ssm start-session`'s real
    behavior for AWS-StartInteractiveCommand: an interactive session
    that drains its own stdin and forwards it to the wrapped command's
    interpreter, then always exits 0 itself regardless of the wrapped
    program's real exit status (the documented session-manager-plugin
    limitation).

    ec2-ssm.sh base64-wraps its `command=[...]` value (same idiom as
    ec2_remote_exec in ec2-lib.sh, for the same reason — the wrapper
    contains literal double quotes that would otherwise have to survive
    the CLI's own JSON-array parsing): the stub decodes and execs it via
    `bash -c`, with the interactive session's own stdin (the caller's
    real, potentially multi-KB payload) forwarded straight through to
    whatever interpreter (`python3 -` / `sh -s`) that decoded command
    invokes — mirroring what the real session-manager-plugin does.
    """
    stub = bin_dir / "aws"
    if stall:
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$@\" >> {bin_dir}/aws.log\n"
            "cat >/dev/null\n"
            "sleep 600\n"
        )
        stub.chmod(0o755)
        return stub

    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {bin_dir}/aws.log\n"
        'for arg in "$@"; do\n'
        '  if [[ "$arg" == command=* ]]; then\n'
        '    inner="${arg#command=[\\\"}"\n'
        '    inner="${inner%\\\"]}"\n'
        '    bash -c "$inner"\n'
        "  fi\n"
        "done\n"
        # SSM's own process always exits 0 regardless of the wrapped
        # program's real exit status.
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


def test_ec2_ssm_sh_exists_and_is_executable():
    assert EC2_SSM_SH.is_file()
    assert os.access(EC2_SSM_SH, os.X_OK)


def test_ec2_ssm_sh_defines_transport_functions():
    src = EC2_SSM_SH.read_text()
    assert "ec2_launch_detached()" in src
    assert "ec2_attach()" in src


# --- ec2_launch_detached -----------------------------------------------


def test_ec2_launch_detached_pipes_payload_and_uses_start_interactive_command(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    # ec2_launch_detached bootstraps `python3 -`, so the piped payload
    # must be valid Python — mirrors the real launch-wrapper script.
    payload = "print('launched-ok')"
    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"{payload}\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "launched-ok" in result.stdout
    assert "__LEERIE_EC2_RC__" not in result.stdout

    log = (bin_dir / "aws.log").read_text()
    assert "ssm" in log
    assert "start-session" in log
    assert "--target i-0123456789abcdef0" in log
    assert "AWS-StartInteractiveCommand" in log
    assert "flyctl" not in log


def test_ec2_launch_detached_propagates_rc_75_flock_loser_pivot(tmp_path):
    """rc=75 (State.__init__'s EXIT_LOCKED / the launch wrapper's own
    flock fast-path) must survive the SSM round trip uncorrupted — this
    is the exact rc the launcher's smart-resume pivot branches on."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    payload = "import sys; sys.exit(75)"
    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"{payload}\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
    )
    assert result.returncode == 75


def test_ec2_launch_detached_propagates_other_nonzero_rc(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"import sys; sys.exit(42)\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
    )
    assert result.returncode == 42


def test_ec2_launch_detached_fails_closed_on_empty_instance_id(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"print('should-not-run')\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "",
        },
    )
    assert result.returncode == 1
    assert "LEERIE_EC2_INSTANCE_ID" in result.stderr
    assert not (bin_dir / "aws.log").exists()


def test_ec2_launch_detached_timeout_yields_124_or_137(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir, stall=True)
    _stub_timeout(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"pass\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_SEED_TIMEOUT_S": "1",
        },
    )
    assert result.returncode in (124, 137), (
        f"expected timeout rc 124/137, got {result.returncode}: {result.stderr}"
    )


def test_ec2_launch_detached_passes_profile_and_region(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' \"pass\" | ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_AWS_PROFILE": "myprofile",
            "LEERIE_AWS_REGION": "us-west-2",
        },
    )
    assert result.returncode == 0, result.stderr
    log = (bin_dir / "aws.log").read_text()
    assert "--profile myprofile" in log
    assert "--region us-west-2" in log


def test_ec2_launch_detached_handles_large_payload_over_4kb(tmp_path):
    """The whole point of piping via stdin rather than embedding in
    --parameters: a payload well over SSM's ~4 KB document-parameter
    ceiling must still round-trip cleanly."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    # ~8 KB payload, comfortably over the 4 KB document-parameter cap.
    # Valid Python — ec2_launch_detached bootstraps `python3 -`.
    filler = "# " + ("x" * 100) + "\n"
    payload = (filler * 80) + "print('LARGE_PAYLOAD_OK')"
    assert len(payload) > 4096

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"ec2_launch_detached",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
        input=payload,
    )
    assert result.returncode == 0, result.stderr
    assert "LARGE_PAYLOAD_OK" in result.stdout


# --- ec2_attach ----------------------------------------------------------


def test_ec2_attach_uses_sh_s_bootstrap(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' 'echo attached-ok' | ec2_attach",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "attached-ok" in result.stdout

    log = (bin_dir / "aws.log").read_text()
    assert "AWS-StartInteractiveCommand" in log
    assert "flyctl" not in log
    # The `command=[...]` value is base64-wrapped (see ec2-ssm.sh's
    # docstring on why: the raw wrapper has literal double quotes that
    # can't survive the CLI's own JSON-array parsing) — decode it back
    # to confirm the "sh -s" bootstrap is really what got sent, rather
    # than asserting on plaintext that's no longer in the log.
    assert "bash <(echo " in log
    b64_start = log.index("bash <(echo ") + len("bash <(echo ")
    b64_end = log.index(" | base64 -d)", b64_start)
    decoded = base64.b64decode(log[b64_start:b64_end]).decode()
    assert decoded.startswith("sh -s;")


def test_ec2_attach_propagates_nonzero_rc(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' 'exit 17' | ec2_attach",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
    )
    assert result.returncode == 17


def test_ec2_attach_fails_closed_on_empty_instance_id(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' 'echo should-not-run' | ec2_attach",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "",
        },
    )
    assert result.returncode == 1
    assert "LEERIE_EC2_INSTANCE_ID" in result.stderr
    assert not (bin_dir / "aws.log").exists()


def test_ec2_attach_timeout_yields_124_or_137(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir, stall=True)
    _stub_timeout(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; "
        f"printf '%s' 'true' | ec2_attach",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_SEED_TIMEOUT_S": "1",
        },
    )
    assert result.returncode in (124, 137), (
        f"expected timeout rc 124/137, got {result.returncode}: {result.stderr}"
    )


def test_ec2_ssm_sourcing_is_idempotent_and_independent(tmp_path):
    """ec2-ssm.sh must be independently sourceable (it pulls in
    ec2-lib.sh itself when not already loaded) and safe to source twice
    without clobbering ec2_remote_exec — mirrors
    ec2-fetch-branch.sh's/ec2-seed-repo.sh's double-source guard."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_interactive_session(bin_dir)

    result = _run_bash(
        f"source {EC2_SSM_SH}; source {EC2_SSM_SH}; "
        f"type ec2_launch_detached >/dev/null && type ec2_remote_exec >/dev/null && echo SOURCED_OK",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "SOURCED_OK" in result.stdout
