"""Zero-config defaults and per-knob overrides in scripts/remote/ec2-defaults.sh,
plus the `ssh -i` identity at the transport sites that use the created key.

Every override test uses a value that DISAGREES with what the discoverable
default would be, and asserts no create-*/describe-* call for that resource,
so a bypass of the knob fails rather than coincidentally matching.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tests.conftest import run_bash
from tests.ec2_stub import _stub_aws, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_SH = REPO_ROOT / "scripts" / "remote" / "ec2-defaults.sh"
LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
EGRESS_IP = "198.51.100.7"

KNOBS = (
    "LEERIE_EC2_INSTANCE_TYPE", "LEERIE_EC2_KEY_NAME", "LEERIE_EC2_SECURITY_GROUP",
    "LEERIE_EC2_SUBNET_ID", "LEERIE_EC2_INSTANCE_PROFILE", "LEERIE_EC2_SSH_CIDR",
    "LEERIE_EC2_SSH_KEY_FILE", "LEERIE_STATE_DIR",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in KNOBS:
        monkeypatch.delenv(k, raising=False)


def _seed(aws_dir: Path, *, default_vpc: bool = True) -> None:
    """Seed VPCs/subnets into the stub's state file."""
    state = json.loads((aws_dir / "state.json").read_text()) if (aws_dir / "state.json").exists() else {}
    state.setdefault("instances", {})
    state.setdefault("volumes", {})
    state["vpcs"] = {"vpc-default0001": {"is_default": default_vpc}} if default_vpc else {
        "vpc-other00002": {"is_default": False}}
    state["subnets"] = {
        "subnet-bbbb": {"vpc_id": "vpc-default0001", "az": "us-east-1b", "public": True},
        "subnet-aaaa": {"vpc_id": "vpc-default0001", "az": "us-east-1a", "public": True},
        "subnet-priv": {"vpc_id": "vpc-default0001", "az": "us-east-1a", "public": False},
        "subnet-elsewhere": {"vpc_id": "vpc-other00002", "az": "us-east-1a", "public": True},
    }
    (aws_dir / "state.json").write_text(json.dumps(state))


def _defaults_env(tmp_path: Path, extra: dict | None = None, *, default_vpc: bool = True):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    _seed(aws_dir, default_vpc=default_vpc)
    curl = aws_dir / "curl"
    curl.write_text(f"#!/bin/sh\necho {EGRESS_IP}\n")
    curl.chmod(0o755)
    state_dir = tmp_path / "state"
    env = {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_EC2_IAM_PROPAGATION_S": "0",
        "HOME": str(tmp_path),
    }
    env.update(extra or {})
    return aws_dir, state_dir, env


def _call(fn: str, env: dict):
    return run_bash(f"source {DEFAULTS_SH}; {fn}", env=env)


def _mutating(aws_dir: Path) -> list[str]:
    return [l for l in read_log(aws_dir) if " create-" in f" {l}" or l.startswith(("ec2 authorize", "ec2 revoke"))]


# --- instance type -----------------------------------------------------------

def test_instance_type_default_and_override(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("default_instance_type", env)
    assert r.returncode == 0 and r.stdout == "m5.xlarge"
    r = _call("default_instance_type", {**env, "LEERIE_EC2_INSTANCE_TYPE": "c7g.large"})
    assert r.stdout == "c7g.large"


# --- subnet ------------------------------------------------------------------

def test_subnet_default_is_public_subnet_in_default_vpc(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("default_subnet_id", env)
    assert r.returncode == 0, r.stderr
    # sorted by AZ: -aaaa wins; private and non-default-VPC subnets never chosen.
    assert r.stdout == "subnet-aaaa"


def test_subnet_override_wins_and_makes_no_aws_call(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SUBNET_ID": "subnet-mine"})
    r = _call("default_subnet_id", env)
    assert r.stdout == "subnet-mine"
    assert read_log(aws_dir) == []


def test_no_default_vpc_fails_naming_the_fix(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, default_vpc=False)
    r = _call("default_subnet_id", env)
    assert r.returncode != 0
    assert r.stdout == ""
    assert "no default VPC" in r.stderr
    assert "aws ec2 create-default-vpc" in r.stderr
    assert "LEERIE_EC2_SUBNET_ID" in r.stderr


# --- security group ----------------------------------------------------------

def test_security_group_created_tagged_with_22_from_egress_cidr_only(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("ensure_security_group", env)
    assert r.returncode == 0, r.stderr
    state = read_state(aws_dir)
    (gid, sg), = state["security_groups"].items()
    assert r.stdout == gid
    assert sg["vpc_id"] == "vpc-default0001"
    assert sg["tags"] == {"leerie": "managed"}
    assert sg["ingress"] == [{"protocol": "tcp", "port": 22, "cidr": f"{EGRESS_IP}/32"}]


def test_security_group_reused_not_duplicated_on_second_run(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    first = _call("ensure_security_group", env).stdout
    second = _call("ensure_security_group", env).stdout
    assert first == second
    assert len(read_state(aws_dir)["security_groups"]) == 1
    assert len([l for l in read_log(aws_dir) if "create-security-group" in l]) == 1
    assert len([l for l in read_log(aws_dir) if "authorize-security-group-ingress" in l]) == 1


def test_security_group_rule_follows_a_changed_egress_ip(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ensure_security_group", env)
    _call("ensure_security_group", {**env, "LEERIE_EC2_SSH_CIDR": "203.0.113.9/32"})
    log = read_log(aws_dir)
    assert any("revoke-security-group-ingress" in l and f"--cidr {EGRESS_IP}/32" in l for l in log)
    (sg,) = read_state(aws_dir)["security_groups"].values()
    assert {"protocol": "tcp", "port": 22, "cidr": "203.0.113.9/32"} in sg["ingress"]


def test_security_group_override_wins_and_creates_nothing(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SECURITY_GROUP": "sg-operator"})
    r = _call("ensure_security_group", env)
    assert r.stdout == "sg-operator"
    assert read_log(aws_dir) == []


def test_open_world_cidr_is_refused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SSH_CIDR": "0.0.0.0/0"})
    r = _call("ensure_security_group", env)
    assert r.returncode != 0
    assert "security_groups" not in read_state(aws_dir) or not read_state(aws_dir)["security_groups"]


# --- key pair ----------------------------------------------------------------

def test_key_pair_created_with_0600_pem_under_state_dir(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path)
    r = _call("ensure_key_pair >/dev/null; echo \"$LEERIE_EC2_KEY_NAME|$LEERIE_EC2_SSH_KEY_FILE\"", env)
    assert r.returncode == 0, r.stderr
    pem = state_dir / "leerie-ssh.pem"
    assert pem.is_file()
    assert stat.S_IMODE(pem.stat().st_mode) == 0o600
    assert "BEGIN RSA PRIVATE KEY" in pem.read_text()
    assert r.stdout.strip().endswith(f"|{pem}")
    assert read_state(aws_dir)["key_pairs"]["leerie-ssh"]["tags"] == {"leerie": "managed"}


def test_key_pair_reused_on_second_run(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path)
    _call("ensure_key_pair", env)
    before = (state_dir / "leerie-ssh.pem").read_text()
    r = _call("ensure_key_pair", env)
    assert r.returncode == 0 and r.stdout == "leerie-ssh"
    assert len([l for l in read_log(aws_dir) if "create-key-pair" in l]) == 1
    assert (state_dir / "leerie-ssh.pem").read_text() == before


def test_key_pair_in_aws_but_pem_missing_fails_with_fix(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path)
    _call("ensure_key_pair", env)
    (state_dir / "leerie-ssh.pem").unlink()
    r = _call("ensure_key_pair", env)
    assert r.returncode != 0
    assert "aws ec2 delete-key-pair --key-name leerie-ssh" in r.stderr
    assert len(read_state(aws_dir)["key_pairs"]) == 1


def test_key_pair_override_wins_and_creates_nothing(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path, {"LEERIE_EC2_KEY_NAME": "my-key"})
    r = _call("ensure_key_pair", env)
    assert r.stdout == "my-key"
    assert read_log(aws_dir) == []
    assert not state_dir.exists()


# --- instance profile --------------------------------------------------------

def test_instance_profile_default_created_and_tagged(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("ensure_instance_profile", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssm"
    state = read_state(aws_dir)
    assert state["instance_profiles"]["leerie-ssm"]["roles"] == ["leerie-ssm"]
    assert state["roles"]["leerie-ssm"]["policies"] == [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    # second call reuses
    n = len([l for l in read_log(aws_dir) if l.startswith("iam create-")])
    assert _call("ensure_instance_profile", env).stdout == "leerie-ssm"
    assert len([l for l in read_log(aws_dir) if l.startswith("iam create-")]) == n


def test_instance_profile_override_wins_and_creates_nothing(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_INSTANCE_PROFILE": "operator-profile"})
    r = _call("ensure_instance_profile", env)
    assert r.stdout == "operator-profile"
    assert not any(l.startswith("iam") for l in read_log(aws_dir))


# --- all knobs set: nothing is created ---------------------------------------

def test_every_knob_set_means_no_create_calls(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path, {
        "LEERIE_EC2_INSTANCE_TYPE": "t3.medium", "LEERIE_EC2_KEY_NAME": "k",
        "LEERIE_EC2_SECURITY_GROUP": "sg-x", "LEERIE_EC2_SUBNET_ID": "subnet-x",
        "LEERIE_EC2_INSTANCE_PROFILE": "p"})
    out = _call("echo $(default_instance_type) $(ensure_key_pair) $(ensure_security_group) "
                "$(default_subnet_id) $(ensure_instance_profile)", env)
    assert out.stdout.strip() == "t3.medium k sg-x subnet-x p"
    assert _mutating(aws_dir) == []


# --- ssh -i at the transport sites -------------------------------------------

def _ssh_stub(bin_dir: Path) -> Path:
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "ssh"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {bin_dir}/ssh.log\ncat >/dev/null\n')
    stub.chmod(0o755)
    return stub


def test_tar_pipe_ssh_argv_carries_identity_before_target(tmp_path):
    bin_dir = tmp_path / "sshbin"
    _ssh_stub(bin_dir)
    key = tmp_path / "leerie-ssh.pem"
    key.write_text("k")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "LEERIE_EC2_SSH_KEY_FILE": str(key)}
    r = run_bash(f"source {LIB_SH}; echo x | ec2_tar_pipe admin@203.0.113.5 /tmp/x", env=env)
    assert r.returncode == 0, r.stderr
    (line,) = (bin_dir / "ssh.log").read_text().splitlines()
    assert f"-i {key} -o IdentitiesOnly=yes" in line
    assert line.index("-i ") < line.index("admin@203.0.113.5")


def test_tar_pipe_without_managed_key_has_no_identity(tmp_path):
    bin_dir = tmp_path / "sshbin"
    _ssh_stub(bin_dir)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin"}
    run_bash(f"source {LIB_SH}; echo x | ec2_tar_pipe admin@h /tmp/x", env=env)
    assert " -i " not in (bin_dir / "ssh.log").read_text()


def test_rsync_ssh_wrapper_embeds_identity(tmp_path):
    key = tmp_path / "leerie-ssh.pem"
    key.write_text("k")
    env = {"PATH": "/usr/bin:/bin", "LEERIE_EC2_SSH_KEY_FILE": str(key)}
    r = run_bash(
        f"source {REPO_ROOT}/scripts/remote/ec2-seed-repo.sh; w=$(_ec2_ssh_wrapper); cat \"$w\"; rm -f \"$w\"",
        env=env)
    assert r.returncode == 0, r.stderr
    exec_line = [l for l in r.stdout.splitlines() if l.startswith("exec ")][0]
    assert f"ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -i '{key}' -o IdentitiesOnly=yes" in exec_line


def test_ami_bake_ssh_opts_lead_with_identity():
    src = (REPO_ROOT / "scripts" / "remote" / "ec2-ami.sh").read_text()
    assert '-i "$keyfile" -o StrictHostKeyChecking=no' in src
    assert src.count('ssh "${ssh_opts[@]}"') >= 2
