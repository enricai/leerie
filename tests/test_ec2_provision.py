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


def _run_bash(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.pop("LEERIE_STATE_DIR", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


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

def test_provision_instance_fails_when_ami_unset(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)
    env["LEERIE_EC2_AMI"] = ""

    result = _run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode != 0
    assert "LEERIE_EC2_AMI" in result.stderr


def test_provision_instance_fails_when_aws_missing():
    result = _run_bash(
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

    result = _run_bash(
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

    result = _run_bash(
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

    result = _run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)

    assert result.returncode != 0
    state = read_state(aws_dir)
    assert state["instances"] == {}
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}


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

    result = _run_bash(
        f"( source {EC2_PROVISION_SH}; provision_instance )",
        env=env,
    )
    assert result.returncode != 0
    assert "terminating instance" not in result.stderr


# --- teardown: idempotent, terminate/stop dispatch ---------------------------

def test_terminate_instance_noop_when_no_instance_id():
    result = _run_bash(
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

    result = _run_bash(
        f"( source {EC2_PROVISION_SH}; "
        "_try_fetch_state_for_ec2_teardown() { return 0; }; "
        "provision_instance )",
        env=env,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "terminating instance" in result.stderr

    state = read_state(aws_dir)
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}


def test_decide_ec2_teardown_leaves_instance_running_on_sync_failure(tmp_path):
    """SAFETY-CRITICAL: if syncing run state to the host fails, the
    instance must be left RUNNING (not terminated/stopped) so the user
    can recover — destroy-then-fetch is a one-way ratchet (mirrors
    provision.sh:262-272)."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
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
    result = _run_bash(f"( {script} )", env=env)
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
    preserves the root EBS volume for later --resume."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
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

    result = _run_bash(
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

    result = _run_bash(
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

    result = _run_bash(f"source {EC2_PROVISION_SH}; provision_instance", env=env)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    sidecar = run_dir / "ec2-instance.json"
    assert sidecar.exists(), "ec2-instance.json sidecar was not written"
    data = json.loads(sidecar.read_text())
    assert data["ec2_instance_id"].startswith("i-")

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json["ec2_instance_id"] == data["ec2_instance_id"]
