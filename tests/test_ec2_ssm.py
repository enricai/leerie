"""Tests for scripts/remote/ec2-ssm.sh (`ec2_launch_detached` / `ec2_attach`
/ the `--shell` session).

Launch and attach go over SSH (DESIGN §6 "Transport substitution for
`flyctl ssh console`"): `ssh … <target> "sudo -n python3 -"` with the
launch wrapper on stdin, and `"sudo -n sh -s"` with the tail wrapper. Not
an SSM AWS-StartInteractiveCommand session — on a real instance its pty
turned `python3 -` into an interactive prompt and the session ended at
host-stdin EOF before the payload was read. SSM is kept for the one
interactive role, `--shell` on a live run.

The ssh stub runs the remote command locally through bash with the
caller's stdin attached, so payload delivery and exit codes (rc=75, the
flock-loser smart-resume pivot) are exercised for real. No real AWS or
SSH calls are made.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.conftest import run_bash
from tests.ec2_stub import write_sudo_shim

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
EC2_SSM_SH = REPO_ROOT / "scripts" / "remote" / "ec2-ssm.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"
SOURCES = f"source {LOG_SH}; source {EC2_LIB_SH}; source {EC2_SSM_SH}; set +e; "


def _stub_ssh(bin_dir: Path) -> Path:
    """Log argv; run the remote command (last argument) through bash with
    this process's stdin, returning its exit status — as sshd would."""
    stub = bin_dir / "ssh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> {bin_dir}/ssh.log\n'
        'for a; do last="$a"; done\n'
        'exec bash -c "$last"\n'
    )
    stub.chmod(0o755)
    write_sudo_shim(bin_dir)
    return stub


def _stub_aws(bin_dir: Path) -> Path:
    stub = bin_dir / "aws"
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> {bin_dir}/aws.log\n')
    stub.chmod(0o755)
    return stub


def _ssh_env(bin_dir: Path, **extra) -> dict:
    return {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(bin_dir.parent),
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
            "LEERIE_EC2_SSH_TARGET": "ec2-user@203.0.113.5", **extra}


def _ssh_lines(bin_dir: Path) -> list[str]:
    p = bin_dir / "ssh.log"
    return p.read_text().splitlines() if p.exists() else []


def test_ec2_ssm_sh_exists_and_is_executable():
    assert EC2_SSM_SH.is_file()
    assert os.access(EC2_SSM_SH, os.X_OK)


def test_ec2_launch_detached_runs_the_payload_over_ssh_as_root(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    _stub_aws(bin_dir)
    result = run_bash(
        SOURCES + "printf '%s' \"print('launched-ok')\" | ec2_launch_detached",
        env=_ssh_env(bin_dir),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "launched-ok\n"
    (line,) = _ssh_lines(bin_dir)
    assert line.endswith("ec2-user@203.0.113.5 sudo -n python3 -")
    assert "BatchMode=yes" in line
    # No SSM session for a non-interactive payload.
    assert not any("ssm" in l for l in (bin_dir / "aws.log").read_text().splitlines()) \
        if (bin_dir / "aws.log").exists() else True


def test_ec2_launch_detached_returns_rc_75_flock_loser_pivot(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    result = run_bash(
        SOURCES + "printf '%s' 'import sys; sys.exit(75)' | ec2_launch_detached",
        env=_ssh_env(bin_dir),
    )
    assert result.returncode == 75


def test_ec2_launch_detached_delivers_a_large_payload_intact(tmp_path):
    # The real launch wrapper is several KB; it must arrive byte-for-byte.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    filler = "\n".join(f"x{i} = {i}" for i in range(2000))
    payload = filler + "\nprint(sum(int(k[1:]) for k in dir() if k.startswith('x') and k[1:].isdigit()))\n"
    script_file = tmp_path / "payload.py"
    script_file.write_text(payload)
    result = run_bash(SOURCES + f"ec2_launch_detached < {script_file}", env=_ssh_env(bin_dir))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(sum(range(2000)))


def test_ec2_attach_streams_the_tail_payload_and_returns_its_rc(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    result = run_bash(
        SOURCES + "printf 'echo tail-one\\necho tail-two\\nexit 17\\n' | ec2_attach",
        env=_ssh_env(bin_dir),
    )
    assert result.returncode == 17
    assert result.stdout == "tail-one\ntail-two\n"
    (line,) = _ssh_lines(bin_dir)
    assert line.endswith("sudo -n sh -s")


def test_ec2_attach_is_not_wrapped_in_the_seed_timeout(tmp_path):
    # A tail is long-lived by design; the seed timeout must not kill it.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    timeout = bin_dir / "timeout"
    timeout.write_text(f'#!/usr/bin/env bash\necho used >> {bin_dir}/timeout.log\nshift; exec "$@"\n')
    timeout.chmod(0o755)
    result = run_bash(SOURCES + "printf 'exit 0' | ec2_attach",
                      env=_ssh_env(bin_dir, LEERIE_SEED_TIMEOUT_S="1"))
    assert result.returncode == 0, result.stderr
    assert not (bin_dir / "timeout.log").exists()


def test_launch_and_attach_fail_closed_on_empty_instance_id(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    for fn in ("ec2_launch_detached", "ec2_attach"):
        result = run_bash(SOURCES + f"printf 'echo should-not-run' | {fn}",
                          env=_ssh_env(bin_dir, LEERIE_EC2_INSTANCE_ID=""))
        assert result.returncode == 1, fn
        assert "LEERIE_EC2_INSTANCE_ID" in result.stderr
        assert "should-not-run" not in result.stdout
    assert _ssh_lines(bin_dir) == []


def test_shell_on_a_live_run_is_an_ssm_session_at_work_as_root(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    _stub_aws(bin_dir)
    result = run_bash(
        SOURCES + "RESUME_SHELL=true; LEERIE_RUN_ID=r1; _attach_to_live_orchestrator_ec2; echo rc=$container_rc",
        env=_ssh_env(bin_dir, LEERIE_AWS_PROFILE="p1", LEERIE_AWS_REGION="us-east-2"),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("rc=130")
    (call,) = (bin_dir / "aws.log").read_text().splitlines()
    assert call == ('ssm start-session --target i-0123456789abcdef0 --document-name '
                    'AWS-StartInteractiveCommand --parameters command=["cd /work && exec sudo -n bash -i"] '
                    '--region us-east-2 --profile p1')
    assert _ssh_lines(bin_dir) == []


def test_tail_on_a_live_run_goes_over_ssh(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub_ssh(bin_dir)
    _stub_aws(bin_dir)
    result = run_bash(
        SOURCES + "render_tail_wrapper() { echo 'echo tailing-$LEERIE_TAIL_RUN_ID'; }; "
        "RESUME_SHELL=false; LEERIE_RUN_ID=r9; _attach_to_live_orchestrator_ec2; echo rc=$container_rc",
        env=_ssh_env(bin_dir),
    )
    assert result.returncode == 0, result.stderr
    assert "tailing-r9" in result.stdout and result.stdout.strip().endswith("rc=130")
    assert _ssh_lines(bin_dir)[-1].endswith("sudo -n sh -s")


def test_ec2_ssm_sourcing_is_idempotent_and_independent(tmp_path):
    """ec2-ssm.sh must be independently sourceable (it pulls in ec2-lib.sh
    itself when not already loaded) and safe to source twice."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    result = run_bash(
        f"source {EC2_SSM_SH}; source {EC2_SSM_SH}; "
        "type ec2_launch_detached >/dev/null && type ec2_remote_exec >/dev/null && echo SOURCED_OK",
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "SOURCED_OK" in result.stdout
