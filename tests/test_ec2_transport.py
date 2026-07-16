"""Tests for the EC2 remote-exec/tar-pipe transport primitive in
scripts/remote/ec2-lib.sh (`ec2_remote_exec` / `ec2_tar_pipe`).

ec2_remote_exec is the `aws ssm start-session` analog of `flyctl ssh
console --pty=false -C "<cmd>"` (single-command exec, remote-rc
recovered from a sentinel line since the session-manager-plugin always
exits 0 regardless of the remote command's actual exit status).
ec2_tar_pipe is the plain-`ssh` analog of the same `tar -czC "$STAGE" .
| flyctl ssh console ... -C "sh -c 'tar -xzC ...'"` idiom used for bulk
stdin transfer (DESIGN §6 names SSH, not SSM, as the transport for this
role — SSM's AWS-StartInteractiveCommand has no stdin-pipe facility and
a ~4 KB document-parameter ceiling that rules out embedding a tar
payload).

Both are exercised via subprocess against stubbed `aws` / `ssh`
binaries, mirroring tests/test_ec2_lib_sh.py's stubbed-`aws` pattern and
tests/test_fetch_branch_sh.py's stubbed-`flyctl` pattern. No real AWS or
SSH calls are made.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _stub_timeout(bin_dir: Path) -> None:
    """Provide a real, killing `timeout` on the test's stubbed PATH.

    These tests pin PATH to `{bin_dir}:/usr/bin:/bin` so the fake `aws`
    is found and the host's Homebrew layout can't leak in. But macOS
    ships no `timeout` in /usr/bin (it's coreutils, via Homebrew), so
    `_seed_timeout_prefix` correctly no-ops — and the stall test's
    `sleep 600` then runs unbounded, hanging the suite for 10 minutes
    rather than failing.

    Unlike `_make_stub_timeout` in tests/test_seed_repo_sh.py, this one
    must honour the cap: the test it serves asserts the timeout *fires*
    (rc 124), so an `exec "$@"` stub that ignores the duration would
    hang exactly like no stub at all.
    """
    stub = bin_dir / "timeout"
    stub.write_text(
        """#!/usr/bin/env bash
# Stub GNU timeout. Runs the child in its own process GROUP and signals
# the whole group on expiry — killing only the direct child is not
# enough: its grandchildren (the stalled stub's own `sleep`) inherit the
# captured stdout, and a $(...) capture blocks until every writer closes
# the pipe, so the caller would hang even though the child is dead.
# Real GNU timeout kills the group for exactly this reason.
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

# `set -m` puts the child in its own process group (pgid == its pid), so
# `kill -- -$pgid` reaches the whole subtree.
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
# GNU timeout reports 124 when it had to kill the child.
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


def _stub_aws_ssm(bin_dir: Path, *, remote_rc: int = 0,
                   remote_stdout: str = "hello-from-instance",
                   stall: bool = False) -> Path:
    """Write a stub `aws` binary that models `ssm start-session`'s
    AWS-StartInteractiveCommand document: echoes SSM's own known
    misbehavior (always exits 0 itself) while running the wrapped
    command string it was handed via --parameters so the rc-sentinel
    the real command embeds actually gets exercised.
    """
    stub = bin_dir / "aws"
    if stall:
        # Simulate a stalled session: never exits on its own. The
        # $(_seed_timeout_prefix) wrapper around this stub is what is
        # supposed to kill it.
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$@\" >> {bin_dir}/aws.log\n"
            "sleep 600\n"
        )
    else:
        # Extract the `command=[...]` parameter's inner string and
        # execute it, mirroring what the real SSM agent does on the
        # instance — the wrapped command (caller's cmd + base64 decode
        # + rc sentinel printf) runs for real so ec2_remote_exec's
        # sentinel-stripping logic is exercised end to end, not just
        # asserted against a canned transcript.
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
            # SSM's own process always exits 0 regardless of the
            # wrapped command's real exit status (the documented
            # session-manager-plugin limitation this transport works
            # around).
            "exit 0\n"
        )
    stub.chmod(0o755)
    return stub


def _stub_ssh(bin_dir: Path, *, rc: int = 0, drain_stdin: bool = True,
              stall: bool = False) -> Path:
    stub = bin_dir / "ssh"
    if stall:
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"echo \"$@\" >> {bin_dir}/ssh.log\n"
            "cat >/dev/null\n"
            "sleep 600\n"
        )
    else:
        body = f'echo "$@" >> {bin_dir}/ssh.log\n'
        if drain_stdin:
            # Extract stdin to a file the test can inspect, mirroring
            # the real ssh target running `tar -xzC ...` against
            # forwarded stdin.
            body += f"cat > {bin_dir}/received_stdin\n"
        body += f"exit {rc}\n"
        stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(0o755)
    return stub


def test_ec2_lib_sh_defines_transport_functions():
    src = EC2_LIB_SH.read_text()
    assert "ec2_remote_exec()" in src
    assert "ec2_tar_pipe()" in src
    assert "_seed_timeout_prefix()" in src


# --- ec2_remote_exec (SSM-based single-command exec) -----------------------


def test_ec2_remote_exec_round_trips_a_command(tmp_path):
    """A command run via ec2_remote_exec reaches the stub and its
    stdout comes back to the caller, sentinel stripped."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_ssm(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'echo remote-hello'",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "remote-hello" in result.stdout
    # The rc sentinel must never leak into the caller-visible output.
    assert "__LEERIE_EC2_RC__" not in result.stdout


def test_ec2_remote_exec_propagates_nonzero_remote_rc(tmp_path):
    """A non-zero remote exit code must propagate to the caller even
    though the session-manager-plugin itself always exits 0."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_ssm(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'exit 42'",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 42


def test_ec2_remote_exec_passes_profile_and_region(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_ssm(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'true'",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_AWS_PROFILE": "myprofile",
            "LEERIE_AWS_REGION": "us-west-2",
        },
    )
    assert result.returncode == 0, result.stderr
    log = (bin_dir / "aws.log").read_text()
    assert "--profile myprofile" in log
    assert "--region us-west-2" in log


def test_ec2_remote_exec_timeout_yields_124_or_137(tmp_path):
    """A stalled SSM session must be killed by the timeout wrapper and
    yield rc 124/137, not hang indefinitely."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_aws_ssm(bin_dir, stall=True)
    # Without this the test hangs for the stub's full `sleep 600`: macOS
    # has no /usr/bin/timeout, so `_seed_timeout_prefix` correctly
    # no-ops and nothing kills the stalled session.
    _stub_timeout(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'true'",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_SEED_TIMEOUT_S": "1",
        },
    )
    assert result.returncode in (124, 137), (
        f"expected timeout rc 124/137, got {result.returncode}: {result.stderr}"
    )


# --- ec2_tar_pipe (SSH-based bulk stdin transfer) ---------------------------


def test_ec2_tar_pipe_streams_stdin_through(tmp_path):
    """A tar pipe's stdin content reaches the remote (stub ssh) side."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir, rc=0)

    payload = "fake-gzipped-tar-bytes"
    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"printf '%s' '{payload}' | ec2_tar_pipe ec2-user@1.2.3.4 /home/leerie",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    received = (bin_dir / "received_stdin").read_text()
    assert received == payload


def test_ec2_tar_pipe_propagates_nonzero_remote_rc(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir, rc=17)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"printf 'x' | ec2_tar_pipe ec2-user@1.2.3.4 /home/leerie",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 17


def test_ec2_tar_pipe_timeout_yields_124_or_137(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir, stall=True)
    # See the note in the remote-exec timeout test: no /usr/bin/timeout
    # on macOS, so without this stub the stalled `ssh` runs unbounded.
    _stub_timeout(bin_dir)

    result = _run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"printf 'x' | ec2_tar_pipe ec2-user@1.2.3.4 /home/leerie",
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "LEERIE_SEED_TIMEOUT_S": "1",
        },
    )
    assert result.returncode in (124, 137), (
        f"expected timeout rc 124/137, got {result.returncode}: {result.stderr}"
    )
