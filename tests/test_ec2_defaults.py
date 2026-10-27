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
        "HOME": str(tmp_path),
        "AWS_REGION": "us-east-1",
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
    r = _call("ec2_default_instance_type", env)
    assert r.returncode == 0 and r.stdout == "m5.xlarge"
    r = _call("ec2_default_instance_type", {**env, "LEERIE_EC2_INSTANCE_TYPE": "c7g.large"})
    assert r.stdout == "c7g.large"


# --- subnet ------------------------------------------------------------------

def test_subnet_default_is_public_subnet_in_default_vpc(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("ec2_default_subnet_id", env)
    assert r.returncode == 0, r.stderr
    # sorted by AZ: -aaaa wins; private and non-default-VPC subnets never chosen.
    assert r.stdout == "subnet-aaaa"


def test_subnet_override_wins_and_makes_no_aws_call(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SUBNET_ID": "subnet-mine"})
    r = _call("ec2_default_subnet_id", env)
    assert r.stdout == "subnet-mine"
    assert read_log(aws_dir) == []


def test_no_default_vpc_fails_naming_the_fix(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, default_vpc=False)
    r = _call("ec2_default_subnet_id", env)
    assert r.returncode != 0
    assert r.stdout == ""
    assert "no default VPC" in r.stderr
    assert "aws ec2 create-default-vpc" in r.stderr
    assert "LEERIE_EC2_SUBNET_ID" in r.stderr


# --- security group ----------------------------------------------------------

def test_security_group_created_tagged_with_22_from_egress_cidr_only(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("ec2_default_security_group", env)
    assert r.returncode == 0, r.stderr
    state = read_state(aws_dir)
    (gid, sg), = state["security_groups"].items()
    assert r.stdout == gid
    assert sg["vpc_id"] == "vpc-default0001"
    assert sg["tags"] == {"leerie:managed": "true"}
    assert sg["ingress"] == [{"protocol": "tcp", "port": 22, "cidr": f"{EGRESS_IP}/32"}]


def test_security_group_reused_not_duplicated_on_second_run(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    first = _call("ec2_default_security_group", env).stdout
    second = _call("ec2_default_security_group", env).stdout
    assert first == second
    assert len(read_state(aws_dir)["security_groups"]) == 1
    assert len([l for l in read_log(aws_dir) if "create-security-group" in l]) == 1
    assert len([l for l in read_log(aws_dir) if "authorize-security-group-ingress" in l]) == 1


def test_security_group_adds_a_changed_egress_ip_and_keeps_the_old_one(tmp_path):
    # Two launchers on different IPs share the group: the second must not
    # cut the first one's SSH transport.
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ec2_default_security_group", env)
    _call("ec2_default_security_group", {**env, "LEERIE_EC2_SSH_CIDR": "203.0.113.9/32"})
    log = read_log(aws_dir)
    assert not any("revoke-security-group-ingress" in l for l in log)
    (sg,) = read_state(aws_dir)["security_groups"].values()
    assert sg["ingress"] == [
        {"protocol": "tcp", "port": 22, "cidr": f"{EGRESS_IP}/32"},
        {"protocol": "tcp", "port": 22, "cidr": "203.0.113.9/32"},
    ]


def test_security_group_created_before_the_tag_rename_is_reused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    state = read_state(aws_dir)
    state["security_groups"] = {"sg-legacy": {
        "name": "leerie-ssh", "vpc_id": "vpc-default0001", "tags": {"leerie": "managed"},
        "ingress": [{"protocol": "tcp", "port": 22, "cidr": f"{EGRESS_IP}/32"}]}}
    (aws_dir / "state.json").write_text(json.dumps(state))
    r = _call("ec2_default_security_group", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "sg-legacy"
    assert _mutating(aws_dir) == []


def test_same_named_security_group_leerie_did_not_create_is_refused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    state = read_state(aws_dir)
    state["security_groups"] = {"sg-theirs": {
        "name": "leerie-ssh", "vpc_id": "vpc-default0001", "tags": {"team": "web"}, "ingress": []}}
    (aws_dir / "state.json").write_text(json.dumps(state))
    r = _call("ec2_default_security_group", env)
    assert r.returncode != 0
    assert "LEERIE_EC2_SECURITY_GROUP" in r.stderr
    assert _mutating(aws_dir) == []


def test_security_group_override_wins_and_creates_nothing(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SECURITY_GROUP": "sg-operator"})
    r = _call("ec2_default_security_group", env)
    assert r.stdout == "sg-operator"
    assert read_log(aws_dir) == []


def test_open_world_cidr_is_refused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_SSH_CIDR": "0.0.0.0/0"})
    r = _call("ec2_default_security_group", env)
    assert r.returncode != 0
    assert "security_groups" not in read_state(aws_dir) or not read_state(aws_dir)["security_groups"]


# --- key pair ----------------------------------------------------------------

def _pem(home: Path, region: str = "us-east-1") -> Path:
    # Account 123456789012 is the stub's sts identity.
    return home / ".leerie" / ".ec2-keys" / "123456789012" / region / "leerie-ssh.pem"


def test_key_pair_created_with_0600_pem_scoped_to_account_and_region(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path)
    r = _call("ec2_default_key_name", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssh"
    pem = _pem(tmp_path)
    assert pem.is_file()
    assert stat.S_IMODE(pem.stat().st_mode) == 0o600
    assert "BEGIN RSA PRIVATE KEY" in pem.read_text()
    assert not state_dir.exists()  # never the per-repo state dir
    assert read_state(aws_dir)["key_pairs"]["leerie-ssh"]["tags"] == {"leerie:managed": "true"}
    path = _call("ec2_default_key_file", env).stdout
    assert path == str(pem)


def test_key_file_differs_per_region_and_is_shared_across_repos(tmp_path):
    _, _, env = _defaults_env(tmp_path)
    east = _call("ec2_default_key_file", env).stdout
    west = _call("ec2_default_key_file", {**env, "AWS_REGION": "us-west-2"}).stdout
    repo_b = _call("ec2_default_key_file", {**env, "LEERIE_STATE_HOST_DIR": str(tmp_path / "other-repo")}).stdout
    assert east == str(_pem(tmp_path)) and west == str(_pem(tmp_path, "us-west-2"))
    assert repo_b == east


def test_second_repo_reuses_the_key_without_error(tmp_path):
    # The defect this pins: the key pair is per account+region, so a second
    # repo (another state dir) must find the same private key.
    aws_dir, _, env = _defaults_env(tmp_path)
    assert _call("ec2_default_key_name", env).returncode == 0
    r = _call("ec2_default_key_name", {**env, "LEERIE_STATE_HOST_DIR": str(tmp_path / "repo-b")})
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssh"
    assert len([l for l in read_log(aws_dir) if "create-key-pair" in l]) == 1


def test_key_pair_reused_on_second_run(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ec2_default_key_name", env)
    before = _pem(tmp_path).read_text()
    r = _call("ec2_default_key_name", env)
    assert r.returncode == 0 and r.stdout == "leerie-ssh"
    assert len([l for l in read_log(aws_dir) if "create-key-pair" in l]) == 1
    assert _pem(tmp_path).read_text() == before


def test_key_pair_created_before_the_tag_rename_is_reused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    state = read_state(aws_dir)
    state["key_pairs"] = {"leerie-ssh": {"name": "leerie-ssh", "tags": {"leerie": "managed"}}}
    (aws_dir / "state.json").write_text(json.dumps(state))
    _pem(tmp_path).parent.mkdir(parents=True)
    _pem(tmp_path).write_text("old")
    r = _call("ec2_default_key_name", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssh" and _mutating(aws_dir) == []


def test_same_named_key_pair_leerie_did_not_create_is_refused(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    state = read_state(aws_dir)
    state["key_pairs"] = {"leerie-ssh": {"name": "leerie-ssh", "tags": {}}}
    (aws_dir / "state.json").write_text(json.dumps(state))
    r = _call("ec2_default_key_name", env)
    assert r.returncode != 0
    assert "LEERIE_EC2_KEY_NAME" in r.stderr
    assert _mutating(aws_dir) == [] and not _pem(tmp_path).exists()


def test_key_pair_in_aws_but_pem_missing_fails_with_fix(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ec2_default_key_name", env)
    _pem(tmp_path).unlink()
    r = _call("ec2_default_key_name", env)
    assert r.returncode != 0
    assert "aws ec2 delete-key-pair --key-name leerie-ssh" in r.stderr
    assert len(read_state(aws_dir)["key_pairs"]) == 1


def test_key_pair_override_wins_and_creates_nothing(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path, {"LEERIE_EC2_KEY_NAME": "my-key"})
    r = _call("ec2_default_key_name", env)
    assert r.stdout == "my-key"
    assert read_log(aws_dir) == []
    assert not (tmp_path / ".leerie").exists()


# --- instance profile --------------------------------------------------------

def test_instance_profile_default_created_and_tagged(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    r = _call("ec2_default_instance_profile", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssm"
    state = read_state(aws_dir)
    assert state["instance_profiles"]["leerie-ssm"]["roles"] == ["leerie-ssm"]
    assert state["roles"]["leerie-ssm"]["policies"] == [
        "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"]
    # second call reuses
    n = len([l for l in read_log(aws_dir) if l.startswith("iam create-")])
    assert _call("ec2_default_instance_profile", env).stdout == "leerie-ssm"
    assert len([l for l in read_log(aws_dir) if l.startswith("iam create-")]) == n


def test_instance_profile_override_wins_and_creates_nothing(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path, {"LEERIE_EC2_INSTANCE_PROFILE": "operator-profile"})
    r = _call("ec2_default_instance_profile", env)
    assert r.stdout == "operator-profile"
    assert not any(l.startswith("iam") for l in read_log(aws_dir))


# --- all knobs set: nothing is created ---------------------------------------

def test_every_knob_set_means_no_create_calls(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path, {
        "LEERIE_EC2_INSTANCE_TYPE": "t3.medium", "LEERIE_EC2_KEY_NAME": "k",
        "LEERIE_EC2_SECURITY_GROUP": "sg-x", "LEERIE_EC2_SUBNET_ID": "subnet-x",
        "LEERIE_EC2_INSTANCE_PROFILE": "p"})
    out = _call("echo $(ec2_default_instance_type) $(ec2_default_key_name) $(ec2_default_security_group) "
                "$(ec2_default_subnet_id) $(ec2_default_instance_profile)", env)
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


def test_identity_falls_back_to_leeries_own_key_without_an_export(tmp_path):
    # A fresh launcher process (resume / finalize / kill) never received
    # LEERIE_EC2_SSH_KEY_FILE; it must still find leerie's key on its own.
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ec2_default_key_name", env)
    r = run_bash(f"source {LIB_SH}; ec2_ssh_identity_args", env=env)
    assert r.stdout.split("\n")[:2] == ["-i", str(_pem(tmp_path))]


def test_identity_does_not_guess_for_an_operator_key(tmp_path):
    aws_dir, _, env = _defaults_env(tmp_path)
    _call("ec2_default_key_name", env)  # leerie's key exists...
    r = run_bash(f"source {LIB_SH}; ec2_ssh_identity_args", env={**env, "LEERIE_EC2_KEY_NAME": "theirs"})
    assert r.stdout == ""  # ...but the operator named another key pair


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


# --- region: whatever preflight accepts, the key path resolves ----------------

def test_region_only_from_aws_default_region_still_resolves_the_key(tmp_path):
    # require_aws accepts AWS_DEFAULT_REGION; `aws configure get region` never
    # reads it. The key path must not be the one place that misses it.
    aws_dir, _, env = _defaults_env(tmp_path)
    env = {**env, "AWS_REGION": "", "LEERIE_AWS_REGION": "", "AWS_DEFAULT_REGION": "eu-west-3"}
    r = _call("ec2_default_key_name >/dev/null && ec2_default_key_file", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == str(_pem(tmp_path, "eu-west-3"))
    assert _pem(tmp_path, "eu-west-3").is_file()


def test_region_precedence_puts_leerie_region_first(tmp_path):
    _, _, env = _defaults_env(tmp_path)
    r = _call("_ec2_region", {**env, "LEERIE_AWS_REGION": "ap-south-1",
                              "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "eu-west-3"})
    assert r.stdout == "ap-south-1"
    r = _call("_ec2_region", {**env, "LEERIE_AWS_REGION": "", "AWS_REGION": "",
                              "AWS_DEFAULT_REGION": "eu-west-3"})
    assert r.stdout == "eu-west-3"


# --- key pair made under the per-repo layout is adopted ----------------------

def test_key_from_the_per_repo_layout_is_moved_into_place(tmp_path):
    aws_dir, state_dir, env = _defaults_env(tmp_path)
    state = read_state(aws_dir)
    state["key_pairs"] = {"leerie-ssh": {"name": "leerie-ssh", "tags": {"leerie": "managed"}}}
    (aws_dir / "state.json").write_text(json.dumps(state))
    state_dir.mkdir()
    (state_dir / "leerie-ssh.pem").write_text("LEGACY-KEY")
    r = _call("ec2_default_key_name", env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "leerie-ssh"
    assert _pem(tmp_path).read_text() == "LEGACY-KEY"
    assert stat.S_IMODE(_pem(tmp_path).stat().st_mode) == 0o600
    assert _mutating(aws_dir) == []          # no new key pair was created


# --- per-instance known_hosts at every ssh site ------------------------------

def test_known_hosts_is_per_instance_at_every_ssh_site(tmp_path):
    bin_dir = tmp_path / "sshbin"
    _ssh_stub(bin_dir)
    home = tmp_path / "home"
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home),
           "LEERIE_EC2_INSTANCE_ID": "i-0aaa", "LEERIE_EC2_SSH_TARGET": "ec2-user@h"}
    kh = f"UserKnownHostsFile={home}/.leerie/.ec2-keys/known_hosts/i-0aaa"
    run_bash(f"source {LIB_SH}; echo x | ec2_tar_pipe ec2-user@h /tmp/x", env=env)
    run_bash(f"source {REPO_ROOT}/scripts/remote/ec2-fetch-branch.sh; _ec2_fetch_ssh 'true'", env=env)
    lines = (bin_dir / "ssh.log").read_text().splitlines()
    assert len(lines) == 2 and all(kh in l for l in lines)
    w = run_bash(
        f"source {REPO_ROOT}/scripts/remote/ec2-seed-repo.sh; w=$(_ec2_ssh_wrapper); cat \"$w\"; rm -f \"$w\"",
        env=env).stdout
    assert f"-o UserKnownHostsFile='{home}/.leerie/.ec2-keys/known_hosts/i-0aaa'" in w
    # A different instance gets a different file, so a recycled IP can't clash.
    other = run_bash(f"source {LIB_SH}; ec2_ssh_known_hosts_args",
                     env={**env, "LEERIE_EC2_INSTANCE_ID": "i-0bbb"}).stdout.split("\n")
    assert other[:2] == ["-o", f"UserKnownHostsFile={home}/.leerie/.ec2-keys/known_hosts/i-0bbb"]


def test_no_instance_id_means_no_known_hosts_override(tmp_path):
    r = run_bash(f"source {LIB_SH}; ec2_ssh_known_hosts_args; _ec2_ssh_known_hosts_string",
                 env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LEERIE_EC2_INSTANCE_ID": ""})
    assert r.returncode == 0 and r.stdout == ""
