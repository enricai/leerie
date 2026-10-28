"""Tests for scripts/remote/ec2-provision.sh.

ec2-provision.sh is sourced (not exec'd) by the leerie launcher's
RUNTIME=ec2 branch. These tests exercise the script's bash logic in
isolation via subprocess, against the stateful `aws` stub in
tests/ec2_stub.py (modeled on tests/test_provision_sh_lifecycle.py +
tests/test_provision_volume.py) so assertions can check for resource
*leaks*, not just which commands were invoked.

Footgun reminder (CLAUDE.md): the state-dir override consumed by
ec2-provision.sh's sidecar writes is LEERIE_STATE_HOST_DIR, NOT
LEERIE_STATE_DIR — the latter silently resolves to the real
~/.leerie/... and assertions against it would pass vacuously.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_bash
from tests.ec2_stub import _stub_aws, leaked_resources, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"

REQUIRED_ENV = {
    "LEERIE_EC2_AMI": "ami-0123456789abcdef0",
    "LEERIE_EC2_INSTANCE_TYPE": "m5.xlarge",
    "LEERIE_EC2_KEY_NAME": "leerie-key",
    "LEERIE_EC2_SECURITY_GROUP": "sg-0123456789abcdef0",
    "LEERIE_EC2_SUBNET_ID": "subnet-0123456789abcdef0",
}


@pytest.fixture(autouse=True)
def _no_leerie_state_dir(monkeypatch):
    """LEERIE_STATE_DIR must never leak into these tests' subprocess env —
    see the footgun reminder above (LEERIE_STATE_HOST_DIR is the knob these
    scripts actually consume)."""
    monkeypatch.delenv("LEERIE_STATE_DIR", raising=False)


def _stub_env(aws_dir: Path, extra: dict | None = None) -> dict:
    env = {
        **REQUIRED_ENV,
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(aws_dir),
    }
    if extra:
        env.update(extra)
    return env


def test_ec2_provision_sh_exists_and_is_executable():
    assert EC2_PROVISION_SH.is_file()
    assert os.access(EC2_PROVISION_SH, os.X_OK)


# --- provision_instance: required-var failures ------------------------------

@pytest.mark.parametrize(
    "blank_var",
    [
        "LEERIE_EC2_INSTANCE_TYPE",
        "LEERIE_EC2_KEY_NAME",
    ],
)
def test_provision_instance_defaults_a_blank_knob(tmp_path, blank_var):
    """Zero-config: a blank instance-shape knob is filled by ec2-defaults.sh
    instead of failing closed; the other knobs still pass through verbatim.
    SG/subnet defaults need a default VPC and are covered in test_ec2_defaults."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)
    env[blank_var] = ""

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert len(_run_instances_calls(aws_dir)) == 1


def test_provision_instance_fails_when_aws_missing():
    result = run_bash(
        f"source {EC2_PROVISION_SH}; provision_instance",
        env={**REQUIRED_ENV, "PATH": "/usr/bin:/bin", "USER_REPO": "/tmp"},
    )
    assert result.returncode != 0
    assert "aws" in result.stderr.lower()


# --- provision_instance: success path ---------------------------------------

def test_provision_instance_exports_instance_id_on_success(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"source {EC2_PROVISION_SH}; provision_instance && echo \"iid=$LEERIE_EC2_INSTANCE_ID\"",
        env=env,
    )

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "iid=i-" in result.stdout

    state = read_state(aws_dir)
    assert len(state["instances"]) == 1
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"


def test_provision_instance_id_parsed_from_real_shaped_output(tmp_path):
    """The stub emits the real `aws ec2 run-instances --output json` shape
    (Instances[0].InstanceId nested under a top-level object) — this pins
    that ec2-provision.sh parses that exact shape, not a flattened one."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"source {EC2_PROVISION_SH}; provision_instance && echo \"iid=$LEERIE_EC2_INSTANCE_ID\"",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"

    log = read_log(aws_dir)
    run_instances_calls = [l for l in log if l.startswith("ec2 run-instances")]
    assert len(run_instances_calls) == 1
    assert "--image-id ami-0123456789abcdef0" in run_instances_calls[0]
    assert "--instance-type m5.xlarge" in run_instances_calls[0]
    assert "--key-name leerie-key" in run_instances_calls[0]

    printed_iid = result.stdout.strip().split("iid=")[-1]
    state = read_state(aws_dir)
    assert printed_iid in state["instances"]


# --- provision_instance: instance profile and public IP in the argv --------

def _run_instances_calls(aws_dir: Path) -> list:
    return [l for l in read_log(aws_dir) if l.startswith("ec2 run-instances")]


def _run_instances_line(aws_dir: Path) -> str:
    calls = _run_instances_calls(aws_dir)
    assert len(calls) == 1, calls
    return calls[0]


def test_instance_profile_knob_reaches_run_instances(tmp_path):
    """An explicit LEERIE_EC2_INSTANCE_PROFILE is the exact name passed as
    `--iam-instance-profile Name=<profile>` (IMPLEMENTATION.md, ec2-provision.sh)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir, {"LEERIE_EC2_INSTANCE_PROFILE": "operator-ssm-profile"})

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "--iam-instance-profile Name=operator-ssm-profile" in _run_instances_line(aws_dir)
    (rec,) = read_state(aws_dir)["instances"].values()
    assert rec["iam_instance_profile"] == "Name=operator-ssm-profile"


def test_explicit_instance_profile_beats_a_differing_default(tmp_path):
    """The operator's profile wins even when the account already holds a
    leerie-created profile of a different name: nothing else may appear."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir, {"LEERIE_EC2_INSTANCE_PROFILE": "operator-profile"})

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    line = _run_instances_line(aws_dir)
    assert line.count("--iam-instance-profile") == 1
    assert "Name=operator-profile" in line
    assert not any(l.startswith("iam create-") for l in read_log(aws_dir))


def test_run_instances_associates_a_public_ip(tmp_path):
    """The SSH bulk-transfer path needs a public IP: run-instances carries a
    --network-interfaces spec with AssociatePublicIpAddress=true, naming the
    resolved subnet and security group (not the bare flags)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode == 0, f"stderr: {result.stderr}"
    line = _run_instances_line(aws_dir)
    assert "--network-interfaces" in line
    assert "AssociatePublicIpAddress=true" in line
    assert "SubnetId=subnet-0123456789abcdef0" in line
    assert "sg-0123456789abcdef0" in line
    assert "AssociatePublicIpAddress=false" not in line


# --- provision_instance: failed create leaks no resources -------------------

def test_failed_create_leaks_no_resources(tmp_path):
    """A run-instances failure must not leave anything behind to reap —
    mirrors provision.sh:684-698's volume-orphan cleanup block, but for
    EC2's case-1 default (root EBS, DeleteOnTermination=true) there is
    nothing created before the instance call that could be orphaned."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    # Replace run-instances with a failing stub.
    stub_path = aws_dir / "aws"
    original = stub_path.read_text()
    failing = original.replace(
        'if action == "run-instances":',
        'if action == "run-instances":\n        sys.exit(1)\n    if False:',
    )
    stub_path.write_text(failing)
    env = _stub_env(aws_dir)

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode != 0
    state = read_state(aws_dir)
    assert state["instances"] == {}
    leaked = leaked_resources(state)
    # Tagged SG/key/role defaults are deliberately persistent; only compute leaks.
    assert (leaked["instances"], leaked["volumes"]) == ({}, {})


def test_failed_create_does_not_register_teardown_trap(tmp_path):
    """The EXIT trap must only be registered AFTER a successful create —
    a failed provision_instance in a subshell must not fire
    decide_ec2_teardown (there's no instance id to tear down)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    stub_path = aws_dir / "aws"
    original = stub_path.read_text()
    failing = original.replace(
        'if action == "run-instances":',
        'if action == "run-instances":\n        sys.exit(1)\n    if False:',
    )
    stub_path.write_text(failing)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"( source {EC2_PROVISION_SH}; provision_instance )",
        env=env,
    )
    assert result.returncode != 0
    assert "terminating instance" not in result.stderr


# --- teardown: idempotent, terminate/stop dispatch ---------------------------

def test_terminate_instance_noop_when_no_instance_id():
    result = run_bash(
        f"source {EC2_PROVISION_SH}; LEERIE_EC2_INSTANCE_ID=''; terminate_instance; echo 'ok'",
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_decide_ec2_teardown_terminates_on_clean_exit(tmp_path):
    """The EXIT trap fires decide_ec2_teardown -> terminate_instance on a
    clean (rc=0) exit, so no instance leaks."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "terminating instance" in result.stderr

    state = read_state(aws_dir)
    leaked = leaked_resources(state)
    # Tagged SG/key/role defaults are deliberately persistent; only compute leaks.
    assert (leaked["instances"], leaked["volumes"]) == ({}, {})


def test_decide_ec2_teardown_leaves_instance_running_on_sync_failure(tmp_path):
    """SAFETY-CRITICAL: if syncing run state to the host fails, the
    instance must be left RUNNING (not terminated/stopped) so the user
    can recover — destroy-then-fetch is a one-way ratchet (mirrors
    provision.sh:262-272)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 1; }; "
        "provision_instance )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "LEFT RUNNING" in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"


def test_teardown_fetch_happens_before_terminate(tmp_path):
    """The load-bearing ordering: the run branch must be fetched to the
    host BEFORE the instance is terminated."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)
    marker = tmp_path / "fetch_called_before_terminate"

    script = f"""
source {EC2_PROVISION_SH}
_try_fetch_state_for_ec2_teardown() {{
  state="$(aws ec2 describe-instances --instance-ids "$LEERIE_EC2_INSTANCE_ID" 2>/dev/null)"
  echo "$state" | grep -q running && touch {marker}
  return 0
}}
provision_instance
"""
    result = run_bash(f"( {script} )", env=env)
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # marker only gets touched if the instance was still "running"
    # (i.e. not yet terminated) at the moment the fetch hook ran.
    assert marker.exists(), (
        "fetch hook did not observe the instance as running before "
        "teardown — fetch must precede terminate"
    )
    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated"


def test_decide_ec2_teardown_pauses_on_unknown_rc(tmp_path):
    """An unclassified non-zero rc stops (not terminates) the instance —
    preserves the root EBS volume for later resume."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "provision_instance; "
        "export LEERIE_REMOTE_EXIT_RC=1 )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "PAUSED" in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "stopped"


def test_decide_ec2_teardown_detaches_on_sigint_rc(tmp_path):
    """rc=130 (host-side SIGINT) leaves the instance alone entirely —
    the orchestrator keeps running detached."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "provision_instance; "
        "export LEERIE_REMOTE_EXIT_RC=130 )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "detached from run" in result.stderr

    state = read_state(aws_dir)
    (iid, rec), = state["instances"].items()
    assert rec["state"] == "running"


def test_decide_ec2_teardown_is_idempotent(tmp_path):
    """A second call to decide_ec2_teardown (simulating a signal-then-exit
    double-fire) must not re-run teardown logic."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = run_bash(
        f"source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance; "
        "decide_ec2_teardown; "
        "export LEERIE_REMOTE_EXIT_RC=1; "
        "decide_ec2_teardown; "
        "echo \"done\"",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "done" in result.stdout
    # Only one terminate call — the second decide_ec2_teardown call was a
    # no-op due to LEERIE_TEARDOWN_DONE, so rc=1 never got to pause it.
    log = read_log(aws_dir)
    terminate_calls = [l for l in log if l.startswith("ec2 terminate-instances")]
    assert len(terminate_calls) == 1


# --- sidecar writes: ec2-instance.json + run.json ----------------------------

def test_provision_instance_writes_ec2_instance_sidecar(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state_host_dir = tmp_path / "state"
    run_id = "run-abc123"
    run_dir = state_host_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({}))

    env = _stub_env(aws_dir, {
        "LEERIE_STATE_HOST_DIR": str(state_host_dir),
        "LEERIE_RUN_ID": run_id,
    })

    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    sidecar = run_dir / "ec2-instance.json"
    assert sidecar.exists(), "ec2-instance.json sidecar was not written"
    data = json.loads(sidecar.read_text())
    assert data["ec2_instance_id"].startswith("i-")

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json["ec2_instance_id"] == data["ec2_instance_id"]


# --- wait_for_instance_ready: terminal state during phase 1 ------------------


def test_wait_for_instance_ready_returns_early_on_terminal_state(tmp_path):
    """Phase 1 of wait_for_instance_ready polls State.Name until it reads
    `running`. If the instance instead reaches a terminal-shaped state
    (terminated/shutting-down/stopping/stopped) -- e.g. someone else
    stopped or terminated it out from under a concurrent provision -- the
    function must return 1 immediately via its own named branch, distinct
    from (and much faster than) the deadline timeout branch."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {"state": "stopping", "_ip_gen": 1, "public_ip": "203.0.113.11"}
    (aws_dir / "state.json").write_text(json.dumps(state))
    env = _stub_env(aws_dir, {"LEERIE_INSTANCE_START_TIMEOUT": "120"})

    result = run_bash(
        f"source {EC2_PROVISION_SH}; wait_for_instance_ready {iid}",
        env=env,
    )

    assert result.returncode != 0
    assert "cannot proceed" in result.stderr.lower()
    assert "timed out" not in result.stderr.lower()


# --- provision_instance: leerie's key reaches the caller's ssh calls --------

def test_provision_exports_leeries_key_file_to_the_calling_shell(tmp_path, monkeypatch):
    """The key pair is resolved inside $(...), whose exports die with the
    subshell; the seed/fetch ssh calls run later in the caller's shell and
    must still carry -i <leerie's key>. Pinned by running them there."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir, {"LEERIE_EC2_KEY_NAME": "", "AWS_REGION": "us-east-1"})
    env.pop("LEERIE_EC2_SSH_KEY_FILE", None)
    monkeypatch.delenv("LEERIE_EC2_SSH_KEY_FILE", raising=False)

    result = run_bash(
        f"source {EC2_PROVISION_SH}; provision_instance >/dev/null && "
        "echo \"file=$LEERIE_EC2_SSH_KEY_FILE\"; ec2_ssh_identity_args",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    pem = home / ".leerie" / ".ec2-keys" / "123456789012" / "us-east-1" / "leerie-ssh.pem"
    lines = result.stdout.splitlines()
    assert lines[0] == f"file={pem}"
    assert lines[1:5] == ["-i", str(pem), "-o", "IdentitiesOnly=yes"]
    assert "--key-name leerie-ssh" in _run_instances_line(aws_dir)


# --- provision_instance: instance-profile propagation retry ----------------

_IAM_FLAKY_WRAPPER = """#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${DENY_IAM:-}" ] && [ "$1" = "iam" ]; then
  echo "An error occurred (AccessDenied) when calling the $2 operation" >&2
  exit 254
fi
if [ "$1 $2" = "ec2 run-instances" ]; then
  echo "$*" >> "$here/attempts.log"
  n=$(wc -l < "$here/attempts.log")
  if [ "$n" -le "${FAIL_TIMES:-0}" ]; then
    echo "${FAIL_MSG}" >&2
    exit 254
  fi
fi
exec "$here/aws.real" "$@"
"""

_IAM_NOT_READY = ("An error occurred (InvalidParameterValue) when calling the RunInstances "
                  "operation: Value (leerie-ssm) for parameter iamInstanceProfile.name is "
                  "invalid. Invalid IAM Instance Profile name")


def _flaky_aws(tmp_path: Path) -> Path:
    aws_dir = tmp_path / "bin"
    stub = _stub_aws(aws_dir)
    stub.rename(aws_dir / "aws.real")
    wrapper = aws_dir / "aws"
    wrapper.write_text(_IAM_FLAKY_WRAPPER)
    wrapper.chmod(0o755)
    return aws_dir


def _attempts(aws_dir: Path) -> int:
    p = aws_dir / "attempts.log"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_run_instances_retries_while_the_profile_propagates(tmp_path):
    aws_dir = _flaky_aws(tmp_path)
    env = _stub_env(aws_dir, {"LEERIE_EC2_INSTANCE_PROFILE": "leerie-ssm",
                              "FAIL_TIMES": "2", "FAIL_MSG": _IAM_NOT_READY})
    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode == 0, result.stderr
    assert _attempts(aws_dir) == 3
    assert len(read_state(aws_dir)["instances"]) == 1


def test_other_run_instances_errors_are_not_retried(tmp_path):
    aws_dir = _flaky_aws(tmp_path)
    env = _stub_env(aws_dir, {
        "LEERIE_EC2_INSTANCE_PROFILE": "leerie-ssm", "FAIL_TIMES": "5",
        "FAIL_MSG": "An error occurred (InsufficientInstanceCapacity) when calling the RunInstances operation"})
    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode != 0
    assert _attempts(aws_dir) == 1
    assert "InsufficientInstanceCapacity" in result.stderr


def test_profile_retry_gives_up_within_its_budget(tmp_path):
    aws_dir = _flaky_aws(tmp_path)
    env = _stub_env(aws_dir, {"LEERIE_EC2_INSTANCE_PROFILE": "leerie-ssm",
                              "FAIL_TIMES": "100", "FAIL_MSG": _IAM_NOT_READY,
                              "LEERIE_EC2_IAM_RETRY_BUDGET_S": "3"})
    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode != 0
    # step 0 under test counts as 1s per attempt: 3 retries + the first try.
    assert _attempts(aws_dir) == 4
    assert read_state(aws_dir)["instances"] == {}


def test_no_profile_means_no_profile_retry(tmp_path):
    # IAM denied: no profile is passed, so the same error text cannot be a
    # propagation delay and must fail on the first attempt.
    aws_dir = _flaky_aws(tmp_path)
    env = _stub_env(aws_dir, {"FAIL_TIMES": "5", "FAIL_MSG": _IAM_NOT_READY, "DENY_IAM": "1"})
    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode != 0
    assert _attempts(aws_dir) == 1
    assert "--iam-instance-profile" not in (aws_dir / "attempts.log").read_text()


@pytest.mark.parametrize("no_push,expected", [("true", True), ("false", False), ("", False)])
def test_sidecar_records_the_launch_time_no_push_intent(tmp_path, no_push, expected):
    # decide_ec2_teardown reads this; the fetched run.json cannot carry it.
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state_host_dir = tmp_path / "state"
    run_dir = state_host_dir / "runs" / "run-np1"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}")
    env = _stub_env(aws_dir, {"LEERIE_STATE_HOST_DIR": str(state_host_dir),
                              "LEERIE_RUN_ID": "run-np1", "NO_PUSH": no_push})
    result = run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads((run_dir / "ec2-instance.json").read_text())["host_no_push"] is expected
