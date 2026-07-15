"""Self-tests for tests/ec2_stub.py — the resource-tracking `aws` stub.

Pins the state-machine contract downstream EC2 lifecycle tests will rely
on: run-instances creates a tracked resource, stop/start/terminate move
it through the expected states without losing it, create/delete-volume
does the same for volumes, and leaked_resources() correctly separates
torn-down resources from still-live ones.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.ec2_stub import leaked_resources, read_log, read_state, _stub_aws


def _run_aws(aws_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env["PATH"] = f"{aws_dir}:{env.get('PATH', '')}"
    return subprocess.run(
        ["aws", *args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_stub_aws_is_the_only_aws_on_path(tmp_path):
    aws_dir = tmp_path / "bin"
    stub = _stub_aws(aws_dir)

    assert stub.is_file()
    assert os.access(stub, os.X_OK)
    result = _run_aws(aws_dir, "sts", "get-caller-identity")
    assert result.returncode == 0, result.stderr
    # A real endpoint would need network egress and real credentials;
    # the stub answers instantly and offline with a fabricated identity.
    assert "123456789012" in result.stdout


def test_run_instances_records_one_running_instance(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert len(state["instances"]) == 1
    (rec,) = state["instances"].values()
    assert rec["state"] == "running"


def test_stop_instances_moves_to_stopped_without_removing(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))

    result = _run_aws(aws_dir, "ec2", "stop-instances", "--instance-ids", instance_id)

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert instance_id in state["instances"]
    assert state["instances"][instance_id]["state"] == "stopped"


def test_start_instances_moves_stopped_back_to_running(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))
    _run_aws(aws_dir, "ec2", "stop-instances", "--instance-ids", instance_id)

    result = _run_aws(aws_dir, "ec2", "start-instances", "--instance-ids", instance_id)

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert state["instances"][instance_id]["state"] == "running"


def test_terminate_instances_moves_to_terminated(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))

    result = _run_aws(aws_dir, "ec2", "terminate-instances", "--instance-ids", instance_id)

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert state["instances"][instance_id]["state"] == "terminated"


def test_describe_instances_reflects_current_state(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))

    result = _run_aws(aws_dir, "ec2", "describe-instances", "--instance-ids", instance_id)

    assert result.returncode == 0, result.stderr
    assert instance_id in result.stdout
    assert "running" in result.stdout


def test_create_volume_records_available_volume(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    result = _run_aws(aws_dir, "ec2", "create-volume", "--size", "30")

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert len(state["volumes"]) == 1
    (rec,) = state["volumes"].values()
    assert rec["state"] == "available"
    assert rec["size"] == 30


def test_delete_volume_moves_to_deleted(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "create-volume", "--size", "8")
    volume_id = next(iter(read_state(aws_dir)["volumes"]))

    result = _run_aws(aws_dir, "ec2", "delete-volume", "--volume-id", volume_id)

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert state["volumes"][volume_id]["state"] == "deleted"


def test_leaked_resources_empty_after_full_teardown(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))
    _run_aws(aws_dir, "ec2", "create-volume", "--size", "8")
    volume_id = next(iter(read_state(aws_dir)["volumes"]))

    _run_aws(aws_dir, "ec2", "terminate-instances", "--instance-ids", instance_id)
    _run_aws(aws_dir, "ec2", "delete-volume", "--volume-id", volume_id)

    state = read_state(aws_dir)
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}


def test_leaked_resources_flags_untorn_down_instance_and_volume(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    instance_id = next(iter(read_state(aws_dir)["instances"]))
    _run_aws(aws_dir, "ec2", "create-volume", "--size", "8")
    volume_id = next(iter(read_state(aws_dir)["volumes"]))
    # Only stop the instance (not terminate); leave the volume untouched.
    _run_aws(aws_dir, "ec2", "stop-instances", "--instance-ids", instance_id)

    state = read_state(aws_dir)
    leaks = leaked_resources(state)

    assert instance_id in leaks["instances"]
    assert volume_id in leaks["volumes"]


def test_multiple_instances_run_independently(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")
    ids = list(read_state(aws_dir)["instances"])
    assert len(ids) == 2
    assert ids[0] != ids[1]

    _run_aws(aws_dir, "ec2", "terminate-instances", "--instance-ids", ids[0])

    state = read_state(aws_dir)
    assert state["instances"][ids[0]]["state"] == "terminated"
    assert state["instances"][ids[1]]["state"] == "running"


def test_terminate_instances_accepts_multiple_space_separated_ids(tmp_path):
    """Real `aws` CLI syntax is one `--instance-ids` flag followed by every
    space-separated id (argparse nargs="+"), not a repeated flag."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "2")
    ids = list(read_state(aws_dir)["instances"])

    result = _run_aws(aws_dir, "ec2", "terminate-instances", "--instance-ids", *ids)

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert all(state["instances"][iid]["state"] == "terminated" for iid in ids)


def test_log_records_every_invocation(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    _run_aws(aws_dir, "sts", "get-caller-identity")
    _run_aws(aws_dir, "ec2", "run-instances", "--count", "1")

    log = read_log(aws_dir)
    assert len(log) == 2
    assert log[0] == "sts get-caller-identity"
    assert log[1] == "ec2 run-instances --count 1"


def test_no_invocation_reaches_a_real_aws_endpoint(tmp_path):
    """The stub is the only `aws` reachable, and it never performs I/O
    beyond its own state/log files under aws_dir — no sockets, no
    network module use. This is enforced structurally: the stub source
    contains no networking imports.
    """
    from tests.ec2_stub import _STUB_SOURCE

    forbidden = ("socket", "urllib", "http.client", "requests", "boto3")
    for token in forbidden:
        assert token not in _STUB_SOURCE, f"stub references {token!r}"
