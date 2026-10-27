"""scripts/remote/ec2-cleanup.sh and the `leerie prune` AWS section touch
leerie-tagged EC2 resources only.

The stateful stub ignores `tag-key` filters (it returns every resource), so
an untagged lookalike reaches the script exactly as it would if the server
side filter were bypassed: the script's own client-side tag re-check is what
these tests exercise. The stub's `describe-instances` carries no ImageId /
KeyName, so a thin wrapper in front of it serves canned live-instance and
network-interface documents for the in-use skips.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_bash
from tests.ec2_stub import STATE_FILENAME, _stub_aws, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
CLEANUP_SH = REPO_ROOT / "scripts" / "remote" / "ec2-cleanup.sh"
LAUNCHER = REPO_ROOT / "leerie"

MUTATING = ("deregister-image", "delete-snapshot", "delete-key-pair",
            "delete-security-group", "delete-role", "delete-instance-profile")

_WRAPPER = """#!/usr/bin/env bash
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "$1 $2" in
  "ec2 describe-instances")
    echo "$1 $2 $*" >>"$here/wrapper.log"
    [ -f "$here/instances.json" ] && { cat "$here/instances.json"; exit 0; } ;;
  "ec2 describe-network-interfaces")
    [ -f "$here/enis.json" ] && { cat "$here/enis.json"; exit 0; } ;;
esac
exec "$here/aws.real" "$@"
"""

TAGGED = {"leerie": "managed"}
LOOKALIKE = {"Name": "leerie-lookalike", "leerie": "unmanaged"}


def _world(tmp_path: Path, extra_env: dict | None = None):
    aws_dir = tmp_path / "bin"
    stub = _stub_aws(aws_dir)
    stub.rename(aws_dir / "aws.real")
    wrapper = aws_dir / "aws"
    wrapper.write_text(_WRAPPER)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "leerie-key.pem").write_text("pem")
    (state_dir / "other-key.pem").write_text("pem")
    state = {
        "instances": {},
        "volumes": {},
        "images": {
            "ami-tagged": {"state": "available", "created": "2020-01-01T00:00:00Z", "snapshot_id": "snap-tagged", "tags": dict(TAGGED)},
            "ami-tagged2": {"state": "available", "created": "2020-01-01T00:00:00Z", "snapshot_id": "snap-tagged2", "tags": {"leerie:managed": "true"}},
            "ami-untagged": {"state": "available", "created": "2020-01-01T00:00:00Z", "snapshot_id": "snap-untagged", "tags": dict(LOOKALIKE)},
            "ami-bare": {"state": "available", "created": "2020-01-01T00:00:00Z", "snapshot_id": "snap-bare", "tags": {}},
        },
        "snapshots": {
            "snap-tagged": {"state": "completed", "tags": dict(TAGGED)},
            "snap-tagged2": {"state": "completed", "tags": dict(TAGGED)},
            "snap-untagged": {"state": "completed", "tags": dict(LOOKALIKE)},
            "snap-bare": {"state": "completed", "tags": {}},
        },
        "key_pairs": {
            "leerie-key": {"name": "leerie-key", "tags": dict(TAGGED)},
            "other-key": {"name": "other-key", "tags": dict(LOOKALIKE)},
        },
        "security_groups": {
            "sg-tagged": {"name": "leerie-ssh", "vpc_id": "vpc-1", "ingress": [], "tags": dict(TAGGED)},
            "sg-untagged": {"name": "leerie-ssh", "vpc_id": "vpc-1", "ingress": [], "tags": dict(LOOKALIKE)},
        },
        "roles": {"leerie-ssm": {"policies": [], "tags": dict(TAGGED)}},
        "instance_profiles": {"leerie-ssm": {"roles": ["leerie-ssm"], "tags": dict(TAGGED)}},
    }
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))
    env = {
        "PATH": f"{aws_dir}:{os.environ['PATH']}",
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "HOME": str(tmp_path),
        "AWS_REGION": "us-east-1",
    }
    env.update(extra_env or {})
    return aws_dir, state_dir, env


def _cleanup(env: dict, args: str = ""):
    return run_bash(f"source {CLEANUP_SH}; ec2_cleanup {args}", env=env)


def _mutations(aws_dir: Path) -> list[str]:
    return [l for l in read_log(aws_dir) if any(f" {m} " in f" {l} " for m in MUTATING)]


def _untagged_view(state: dict) -> str:
    """Byte-stable serialisation of everything that is NOT leerie-tagged."""
    keep = {}
    for sect in ("images", "snapshots", "key_pairs", "security_groups"):
        keep[sect] = {k: v for k, v in state.get(sect, {}).items()
                      if not (v.get("tags", {}).get("leerie") == "managed"
                              or v.get("tags", {}).get("leerie:managed") == "true")}
    return json.dumps(keep, sort_keys=True)


def test_dry_run_is_default_and_changes_nothing(tmp_path):
    aws_dir, state_dir, env = _world(tmp_path)
    before = read_state(aws_dir)
    r = _cleanup(env)
    assert r.returncode == 0, r.stderr
    assert read_state(aws_dir) == before
    assert _mutations(aws_dir) == []
    assert (state_dir / "leerie-key.pem").exists()
    for rid in ("ami-tagged", "ami-tagged2", "snap-tagged", "leerie-key", "sg-tagged"):
        assert f"would delete" in r.stdout and rid in r.stdout
    assert "deleting" not in r.stdout
    for rid in ("ami-untagged", "ami-bare", "other-key", "sg-untagged"):
        assert rid not in r.stdout


def test_apply_removes_exactly_the_tagged_set(tmp_path):
    aws_dir, state_dir, env = _world(tmp_path)
    untagged_before = _untagged_view(read_state(aws_dir))
    r = _cleanup(env, "--apply")
    assert r.returncode == 0, r.stderr
    state = read_state(aws_dir)
    assert state["images"]["ami-tagged"]["state"] == "deregistered"
    assert state["images"]["ami-tagged2"]["state"] == "deregistered"
    assert state["snapshots"]["snap-tagged"]["state"] == "deleted"
    assert state["snapshots"]["snap-tagged2"]["state"] == "deleted"
    assert "leerie-key" not in state["key_pairs"]
    assert not (state_dir / "leerie-key.pem").exists()
    assert "sg-tagged" not in state["security_groups"]
    assert "leerie-ssm" not in state["roles"]
    assert "leerie-ssm" not in state["instance_profiles"]
    # untagged lookalikes: byte-identical, and no call ever named them
    assert _untagged_view(state) == untagged_before
    assert (state_dir / "other-key.pem").exists()
    for l in _mutations(aws_dir):
        for forbidden in ("ami-untagged", "ami-bare", "snap-untagged", "snap-bare",
                          "other-key", "sg-untagged"):
            assert forbidden not in l, l


def test_untagged_snapshot_of_a_tagged_ami_is_not_deleted(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    state = read_state(aws_dir)
    state["snapshots"]["snap-tagged"]["tags"] = {}
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))
    assert _cleanup(env, "--apply").returncode == 0
    state = read_state(aws_dir)
    assert state["images"]["ami-tagged"]["state"] == "deregistered"
    assert state["snapshots"]["snap-tagged"]["state"] == "completed"
    assert not any("delete-snapshot --snapshot-id snap-tagged " in l + " "
                   for l in read_log(aws_dir))


def test_ami_in_use_by_a_live_instance_is_not_deregistered(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    (aws_dir / "instances.json").write_text(json.dumps({"Reservations": [{"Instances": [
        {"InstanceId": "i-1", "ImageId": "ami-tagged", "KeyName": "leerie-key"}]}]}))
    r = _cleanup(env, "--apply")
    assert r.returncode == 0, r.stderr
    state = read_state(aws_dir)
    assert state["images"]["ami-tagged"]["state"] == "available"
    assert state["snapshots"]["snap-tagged"]["state"] == "completed"
    assert "skip AMI ami-tagged (in use by an instance)" in r.stdout
    assert "leerie-key" in state["key_pairs"]
    assert "skip key pair leerie-key" in r.stdout
    assert state["images"]["ami-tagged2"]["state"] == "deregistered"


def test_security_group_still_attached_is_skipped(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    (aws_dir / "enis.json").write_text(json.dumps({"NetworkInterfaces": [{"NetworkInterfaceId": "eni-1"}]}))
    r = _cleanup(env, "--apply")
    assert "skip security group sg-tagged (still attached)" in r.stdout
    assert "sg-tagged" in read_state(aws_dir)["security_groups"]


def test_iam_role_in_use_is_skipped(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    (aws_dir / "instances.json").write_text(json.dumps({"Reservations": [{"Instances": [
        {"InstanceId": "i-1", "IamInstanceProfile": {"Arn": "arn:aws:iam::1:instance-profile/leerie-ssm"}}]}]}))
    r = _cleanup(env, "--apply")
    assert "skip IAM role/instance profile leerie-ssm" in r.stdout
    assert "leerie-ssm" in read_state(aws_dir)["roles"]


def test_untagged_iam_role_is_left_alone(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    state = read_state(aws_dir)
    state["roles"]["leerie-ssm"]["tags"] = {}
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))
    assert _cleanup(env, "--apply").returncode == 0
    assert "leerie-ssm" in read_state(aws_dir)["roles"]
    assert not any(" delete-role " in f" {l} " for l in read_log(aws_dir))


def test_keep_current_spares_matching_ami_only(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    r = run_bash(f"""source {CLEANUP_SH}
ec2_ami_version() {{ echo v1; }}
ec2_ami_source_hash() {{ echo h1; }}
ec2_cleanup --apply --keep-current""", env=env)
    assert r.returncode == 0, r.stderr
    state = read_state(aws_dir)
    # neither carries version/hash tags, so neither matches v1/h1: both go
    assert state["images"]["ami-tagged"]["state"] == "deregistered"
    state["images"]["ami-tagged2"]["state"] = "available"
    state["images"]["ami-tagged2"]["tags"].update({"leerie:version": "v1", "leerie:source-hash": "h1"})
    (aws_dir / STATE_FILENAME).write_text(json.dumps(state))
    r = run_bash(f"""source {CLEANUP_SH}
ec2_ami_version() {{ echo v1; }}
ec2_ami_source_hash() {{ echo h1; }}
ec2_cleanup --apply --keep-current""", env=env)
    assert "skip AMI ami-tagged2 (current version+hash AMI kept)" in r.stdout
    assert read_state(aws_dir)["images"]["ami-tagged2"]["state"] == "available"


def test_report_never_deletes(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    before = read_state(aws_dir)
    r = run_bash(f"source {CLEANUP_SH}; ec2_cleanup_report", env=env)
    assert r.returncode == 0, r.stderr
    assert read_state(aws_dir) == before
    assert _mutations(aws_dir) == []


def test_unknown_option_is_rejected_without_aws_calls(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    assert _cleanup(env, "--bogus").returncode == 2
    assert read_log(aws_dir) == []


# --- launcher `prune` integration -------------------------------------------

def _prune(tmp_path: Path, env: dict, *args: str):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    root = tmp_path / "leerie-root"
    root.mkdir(exist_ok=True)
    e = {**os.environ, **env, "LEERIE_STATE_DIR": str(root),
         "LEERIE_STATE_HOST_DIR": str(root), "USER_REPO": str(repo)}
    return subprocess.run(["bash", str(LAUNCHER), "prune", *args],
                          capture_output=True, text=True, env=e, cwd=str(repo))


def test_prune_without_credentials_skips_aws_and_succeeds(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
              "AWS_PROFILE"):
        env[k] = ""
    env["AWS_REGION"] = ""
    env["AWS_DEFAULT_REGION"] = ""
    r = _prune(tmp_path, env, "--apply")
    assert r.returncode == 0, r.stderr
    assert "no credentials resolved" in r.stdout
    assert _mutations(aws_dir) == []


def test_prune_with_credentials_dry_run_lists_tagged_and_deletes_nothing(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    env.update(AWS_ACCESS_KEY_ID="AKIASTUB", AWS_SECRET_ACCESS_KEY="x", AWS_REGION="us-east-1")
    before = read_state(aws_dir)
    r = _prune(tmp_path, env)
    assert r.returncode == 0, r.stderr
    assert "EC2 leerie-tagged resources (dry-run)" in r.stdout
    assert read_state(aws_dir) == before
    assert _mutations(aws_dir) == []


def test_prune_apply_deletes_tagged_only(tmp_path):
    aws_dir, _, env = _world(tmp_path)
    env.update(AWS_ACCESS_KEY_ID="AKIASTUB", AWS_SECRET_ACCESS_KEY="x", AWS_REGION="us-east-1")
    untagged_before = _untagged_view(read_state(aws_dir))
    r = _prune(tmp_path, env, "--apply", "--older-than", "0")
    assert r.returncode == 0, r.stderr
    assert "EC2 leerie-tagged resources (apply)" in r.stdout
    state = read_state(aws_dir)
    assert state["images"]["ami-tagged"]["state"] == "deregistered"
    assert _untagged_view(state) == untagged_before
