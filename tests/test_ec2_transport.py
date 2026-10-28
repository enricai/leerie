"""Tests for the EC2 remote-exec/tar-pipe transport primitive in
scripts/remote/ec2-lib.sh (`ec2_remote_exec` / `ec2_tar_pipe`).

Both go over SSH (DESIGN §6 "Transport substitution for `flyctl ssh
console`"). ec2_remote_exec is the analog of `flyctl ssh console
--pty=false -C "<cmd>"`: `ssh … <target> "sudo -n bash -c <cmd>"`, the
caller's stdin passed through and the remote exit status returned
natively. It is not an SSM session: on a real instance a payload piped
into AWS-StartInteractiveCommand met a pty-backed interpreter and the
session ended at host-stdin EOF. ec2_tar_pipe is the analog of the
`tar -czC "$STAGE" . | flyctl ssh console ... -C "sh -c 'tar -xzC ...'"`
bulk-transfer idiom.

Both are exercised via subprocess against stubbed `ssh` / `sudo` / `aws`
binaries; the ssh stub runs the remote command locally, so quoting, stdin
and exit codes are exercised for real. No real AWS or SSH calls are made.
"""
from __future__ import annotations

from pathlib import Path

from tests.conftest import run_bash
from tests.ec2_stub import write_sudo_shim
from tests.stub_helpers import _stub_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _stub_ssh_exec(bin_dir: Path, *, stall: bool = False) -> Path:
    """An `ssh` that logs its argv and runs the remote command (its last
    argument) locally through bash, as the instance's login shell would,
    with this process's stdin attached and its exit status returned."""
    stub = bin_dir / "ssh"
    body = f'printf "%s\\n" "$*" >> {bin_dir}/ssh.log\n'
    if stall:
        body += "cat >/dev/null\nsleep 600\n"
    else:
        body += 'for a; do last="$a"; done\nexec bash -c "$last"\n'
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(0o755)
    write_sudo_shim(bin_dir)
    return stub


def _exec_env(bin_dir: Path, **extra) -> dict:
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(bin_dir.parent),
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@203.0.113.5", **extra}


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
    # _seed_timeout_prefix's single definition site is
    # scripts/remote/seed-common.sh, sourced by ec2-lib.sh — check it is
    # reachable after sourcing ec2-lib.sh rather than defined inline here.
    result = run_bash(f"source {EC2_LIB_SH}; type _seed_timeout_prefix", env={})
    assert result.returncode == 0, result.stderr
    assert "_seed_timeout_prefix is a function" in result.stdout


# --- ec2_remote_exec (SSH single-command exec) ------------------------------


def test_ec2_remote_exec_round_trips_a_command(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'echo remote-hello'",
        env=_exec_env(bin_dir),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "remote-hello\n"
    (line,) = (bin_dir / "ssh.log").read_text().splitlines()
    assert line.endswith("ec2-user@203.0.113.5 sudo -n bash -c echo\\ remote-hello")
    assert "-o BatchMode=yes" in line and "StrictHostKeyChecking=accept-new" in line


def test_ec2_remote_exec_returns_the_remote_exit_code(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'exit 42'",
        env=_exec_env(bin_dir),
    )
    assert result.returncode == 42


def test_ec2_remote_exec_passes_stdin_and_keeps_quoting(tmp_path):
    # The accept-blocked / flock-probe shape: a program on stdin, argv with
    # quotes and spaces. Both must survive the remote login shell intact.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        """printf 'import sys\\nprint(sys.stdin.read() or "no-stdin", sys.argv[1:])\\n' > /dev/null; """
        """printf '%s' 'import sys; print("prog-ran", sys.argv[1:]); raise SystemExit(75)' """
        """| ec2_remote_exec i-0123456789abcdef0 "python3 - 'a b' \\"it's\\"" """,
        env=_exec_env(bin_dir),
    )
    assert result.returncode == 75
    assert result.stdout == "prog-ran ['a b', \"it's\"]\n"


def test_ec2_remote_exec_resolves_the_target_for_another_instance(tmp_path):
    # LEERIE_EC2_SSH_TARGET belongs to a different instance: the address
    # is looked up for the one asked for (a verb that never resumed).
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    aws = bin_dir / "aws"
    aws.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {bin_dir}/aws.log\n'
        """echo '{"Reservations":[{"Instances":[{"InstanceId":"i-0other","PublicIpAddress":"198.51.100.9"}]}]}'\n"""
    )
    aws.chmod(0o755)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; ec2_remote_exec i-0other 'true'",
        env=_exec_env(bin_dir),
    )
    assert result.returncode == 0, result.stderr
    assert "ec2-user@198.51.100.9 sudo -n bash -c true" in (bin_dir / "ssh.log").read_text()
    assert "describe-instances --instance-ids i-0other" in (bin_dir / "aws.log").read_text()


def test_ec2_remote_exec_fails_closed_without_an_address(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    aws = bin_dir / "aws"
    aws.write_text('#!/usr/bin/env bash\necho \'{"Reservations":[{"Instances":[{"InstanceId":"i-0x"}]}]}\'\n')
    aws.chmod(0o755)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; ec2_remote_exec i-0x 'echo nope'",
        env=_exec_env(bin_dir, LEERIE_EC2_SSH_TARGET=""),
    )
    assert result.returncode == 1
    assert "no SSH address for instance i-0x" in result.stderr
    assert not (bin_dir / "ssh.log").exists()


def test_ec2_remote_exec_makes_no_ssm_call(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    aws = bin_dir / "aws"
    aws.write_text(f'#!/usr/bin/env bash\necho "$*" >> {bin_dir}/aws.log\n')
    aws.chmod(0o755)
    r = run_bash(f"source {LOG_SH}; source {EC2_LIB_SH}; ec2_remote_exec i-0123456789abcdef0 'true'",
                 env=_exec_env(bin_dir))
    assert r.returncode == 0, r.stderr
    calls = (bin_dir / "aws.log").read_text().splitlines() if (bin_dir / "aws.log").exists() else []
    # Only the identity lookup for leerie's key path; never a session.
    assert all(c.startswith("sts get-caller-identity") or c.startswith("configure get") for c in calls), calls
    assert not any("ssm" in c for c in calls)


def test_ec2_remote_exec_timeout_yields_124_or_137(tmp_path):
    """A stalled SSH session must be killed by the timeout wrapper and
    yield rc 124/137, not hang indefinitely."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir, stall=True)
    # macOS has no /usr/bin/timeout, so `_seed_timeout_prefix` would no-op.
    _stub_timeout(bin_dir)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; "
        f"ec2_remote_exec i-0123456789abcdef0 'true'",
        env=_exec_env(bin_dir, LEERIE_SEED_TIMEOUT_S="1"),
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
    result = run_bash(
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

    result = run_bash(
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

    result = run_bash(
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


# --- neither transport lands as root: remote side runs under sudo -n --------

def _logging_sudo(bin_dir: Path, *, deny: bool = False) -> Path:
    """A sudo that records its argv (and can refuse, like a host with no
    grant) — overrides write_sudo_shim's pass-through version."""
    shim = bin_dir / "sudo"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> {bin_dir}/sudo.log\n'
        + ("echo 'sudo: a password is required' >&2; exit 1\n" if deny else "")
        + 'while [ $# -gt 0 ]; do case "$1" in -n) shift ;; *) break ;; esac; done\n'
        'exec "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def test_ec2_remote_exec_runs_the_command_through_sudo(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    _logging_sudo(bin_dir)  # replaces the pass-through shim
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; ec2_remote_exec i-0123456789abcdef0 'echo ran-as-elevated'",
        env=_exec_env(bin_dir),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ran-as-elevated\n"
    (call,) = (bin_dir / "sudo.log").read_text().splitlines()
    assert call == "-n bash -c echo ran-as-elevated"


def test_ec2_remote_exec_fails_when_sudo_is_refused(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh_exec(bin_dir)
    _logging_sudo(bin_dir, deny=True)
    result = run_bash(
        f"source {LOG_SH}; source {EC2_LIB_SH}; ec2_remote_exec i-0123456789abcdef0 'echo should-not-run'",
        env=_exec_env(bin_dir),
    )
    assert result.returncode != 0
    assert "should-not-run" not in result.stdout


def test_ec2_tar_pipe_extracts_under_sudo(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(f'#!/bin/sh\nfor a; do last="$a"; done\nprintf "%s\\n" "$last" > {bin_dir}/remote_cmd\ncat >/dev/null\n')
    ssh.chmod(0o755)
    run_bash(f"source {LOG_SH}; source {EC2_LIB_SH}; echo x | ec2_tar_pipe ec2-user@h /home/leerie",
             env={"PATH": f"{bin_dir}:/usr/bin:/bin"})
    remote = (bin_dir / "remote_cmd").read_text().strip()
    assert remote == "sudo -n sh -c 'mkdir -p '\\''/home/leerie'\\'' && tar -xzC '\\''/home/leerie'\\'''"


def test_fetch_ssh_and_rsync_receiver_run_under_sudo():
    fetch = (REPO_ROOT / "scripts" / "remote" / "ec2-fetch-branch.sh").read_text()
    seed = (REPO_ROOT / "scripts" / "remote" / "ec2-seed-repo.sh").read_text()
    assert '"$LEERIE_EC2_SSH_TARGET" "sudo -n $remote_cmd"' in fetch
    assert '-e "$wrapper" --rsync-path="sudo -n rsync"' in seed
