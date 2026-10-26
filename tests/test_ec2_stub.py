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


# --- images / key pairs / security groups / VPC / IAM -----------------------


def _json(result):
    import json
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_run_instances_records_launch_arguments(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    _run_aws(
        aws_dir, "ec2", "run-instances", "--image-id", "ami-abc",
        "--instance-type", "t3.large", "--key-name", "k1",
        "--subnet-id", "subnet-1", "--security-group-ids", "sg-1", "sg-2",
        "--iam-instance-profile", "Name=leerie-ssm",
        "--associate-public-ip-address",
        "--tag-specifications",
        "ResourceType=instance,Tags=[{Key=leerie:purpose,Value=bake}]",
    )

    rec = next(iter(read_state(aws_dir)["instances"].values()))
    assert rec["image_id"] == "ami-abc"
    assert rec["instance_type"] == "t3.large"
    assert rec["iam_instance_profile"] == "Name=leerie-ssm"
    assert rec["associate_public_ip"] is True
    assert rec["security_group_ids"] == ["sg-1", "sg-2"]
    assert rec["tags"] == {"leerie:purpose": "bake"}


def test_plain_run_instances_state_gains_no_new_keys(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(aws_dir, "ec2", "run-instances")
    state = read_state(aws_dir)
    assert set(state) == {"instances", "volumes"}
    assert set(next(iter(state["instances"].values()))) == {"state", "_ip_gen", "public_ip"}


def test_create_key_pair_then_describe_returns_name(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)

    created = _json(_run_aws(aws_dir, "ec2", "create-key-pair", "--key-name", "leerie-a"))
    assert created["KeyName"] == "leerie-a"
    assert "PRIVATE KEY" in created["KeyMaterial"]

    names = [k["KeyName"] for k in _json(_run_aws(aws_dir, "ec2", "describe-key-pairs"))["KeyPairs"]]
    assert names == ["leerie-a"]
    missing = _run_aws(aws_dir, "ec2", "describe-key-pairs", "--key-names", "nope")
    assert missing.returncode != 0
    assert "InvalidKeyPair.NotFound" in missing.stderr
    dup = _run_aws(aws_dir, "ec2", "create-key-pair", "--key-name", "leerie-a")
    assert dup.returncode != 0

    _run_aws(aws_dir, "ec2", "delete-key-pair", "--key-name", "leerie-a")
    assert _json(_run_aws(aws_dir, "ec2", "describe-key-pairs"))["KeyPairs"] == []


def test_describe_images_filters_by_tag_value(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    for h in ("hash-old", "hash-new"):
        _run_aws(
            aws_dir, "ec2", "create-image", "--name", f"leerie-{h}",
            "--tag-specifications",
            f"ResourceType=image,Tags=[{{Key=leerie:source-hash,Value={h}}}]",
        )

    images = _json(_run_aws(
        aws_dir, "ec2", "describe-images",
        "--filters", "Name=tag:leerie:source-hash,Values=hash-new",
    ))["Images"]
    assert [i["Name"] for i in images] == ["leerie-hash-new"]
    assert images[0]["Tags"] == [{"Key": "leerie:source-hash", "Value": "hash-new"}]
    assert len(_json(_run_aws(aws_dir, "ec2", "describe-images"))["Images"]) == 2


def test_deregister_without_snapshot_delete_leaks_snapshot(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    ami = _json(_run_aws(aws_dir, "ec2", "create-image", "--name", "x"))["ImageId"]
    assert leaked_resources(read_state(aws_dir)) == {"instances": {}, "volumes": {}}

    _run_aws(aws_dir, "ec2", "deregister-image", "--image-id", ami)
    leaks = leaked_resources(read_state(aws_dir))
    assert len(leaks["snapshots"]) == 1
    assert _json(_run_aws(aws_dir, "ec2", "describe-images"))["Images"] == []

    _run_aws(aws_dir, "ec2", "delete-snapshot", "--snapshot-id", next(iter(leaks["snapshots"])))
    assert leaked_resources(read_state(aws_dir)) == {"instances": {}, "volumes": {}}


def test_security_group_lifecycle_and_ingress(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    gid = _json(_run_aws(
        aws_dir, "ec2", "create-security-group", "--group-name", "leerie-sg",
        "--description", "d", "--vpc-id", "vpc-1",
    ))["GroupId"]
    _run_aws(
        aws_dir, "ec2", "authorize-security-group-ingress", "--group-id", gid,
        "--protocol", "tcp", "--port", "22", "--cidr", "198.51.100.7/32",
    )

    groups = _json(_run_aws(
        aws_dir, "ec2", "describe-security-groups", "--filters", "Name=group-name,Values=leerie-sg",
    ))["SecurityGroups"]
    assert groups[0]["GroupId"] == gid
    assert groups[0]["IpPermissions"][0]["IpRanges"] == [{"CidrIp": "198.51.100.7/32"}]
    assert _json(_run_aws(
        aws_dir, "ec2", "describe-security-groups", "--filters", "Name=group-name,Values=other",
    ))["SecurityGroups"] == []
    assert gid in leaked_resources(read_state(aws_dir))["security_groups"]

    _run_aws(aws_dir, "ec2", "delete-security-group", "--group-id", gid)
    assert leaked_resources(read_state(aws_dir)) == {"instances": {}, "volumes": {}}


def test_leaked_resources_reports_bake_instance_keypair_sg_and_orphan_snapshot(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _run_aws(
        aws_dir, "ec2", "run-instances", "--tag-specifications",
        "ResourceType=instance,Tags=[{Key=leerie:purpose,Value=bake}]",
    )
    _run_aws(aws_dir, "ec2", "run-instances")  # not a bake instance
    _run_aws(aws_dir, "ec2", "create-key-pair", "--key-name", "k")
    _run_aws(aws_dir, "ec2", "create-security-group", "--group-name", "g")
    ami = _json(_run_aws(aws_dir, "ec2", "create-image", "--name", "i"))["ImageId"]
    _run_aws(aws_dir, "ec2", "deregister-image", "--image-id", ami)

    leaks = leaked_resources(read_state(aws_dir))
    assert len(leaks["instances"]) == 2
    assert len(leaks["bake_instances"]) == 1
    assert list(leaks["bake_instances"]) == [
        i for i, r in leaks["instances"].items() if r.get("tags")
    ]
    assert list(leaks["key_pairs"]) == ["k"]
    assert len(leaks["security_groups"]) == 1
    assert len(leaks["snapshots"]) == 1


def test_describe_vpcs_and_subnets_are_seedable(tmp_path):
    import json
    from tests.ec2_stub import STATE_FILENAME
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    state = read_state(aws_dir)
    state["vpcs"] = {"vpc-d": {"is_default": True}, "vpc-x": {"is_default": False}}
    state["subnets"] = {
        "subnet-a": {"vpc_id": "vpc-d", "public": True},
        "subnet-b": {"vpc_id": "vpc-x", "public": False},
    }
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))

    vpcs = _json(_run_aws(aws_dir, "ec2", "describe-vpcs", "--filters", "Name=isDefault,Values=true"))["Vpcs"]
    assert [v["VpcId"] for v in vpcs] == ["vpc-d"]
    assert len(_json(_run_aws(aws_dir, "ec2", "describe-vpcs"))["Vpcs"]) == 2
    subnets = _json(_run_aws(aws_dir, "ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=vpc-d"))["Subnets"]
    assert [s["SubnetId"] for s in subnets] == ["subnet-a"]


def test_iam_instance_profile_calls_are_tracked(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    missing = _run_aws(aws_dir, "iam", "get-instance-profile", "--instance-profile-name", "p")
    assert missing.returncode != 0

    _run_aws(aws_dir, "iam", "create-role", "--role-name", "r")
    _run_aws(aws_dir, "iam", "attach-role-policy", "--role-name", "r",
             "--policy-arn", "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    _run_aws(aws_dir, "iam", "create-instance-profile", "--instance-profile-name", "p")
    _run_aws(aws_dir, "iam", "add-role-to-instance-profile",
             "--instance-profile-name", "p", "--role-name", "r")

    got = _json(_run_aws(aws_dir, "iam", "get-instance-profile", "--instance-profile-name", "p"))
    assert got["InstanceProfile"]["InstanceProfileName"] == "p"
    state = read_state(aws_dir)
    assert state["instance_profiles"]["p"]["roles"] == ["r"]
    assert state["roles"]["r"]["policies"] == ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    assert set(leaked_resources(state)) >= {"instance_profiles", "roles"}
