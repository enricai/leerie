import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "ec2-ami.sh"


def _run(tmp_path: Path, aws_out: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    aws = bindir / "aws"
    aws.write_text(f"#!/bin/sh\necho \"$@\" >> {tmp_path}/calls\necho '{aws_out}'\n")
    aws.chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    env.pop("LEERIE_EC2_AMI", None)
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", "-c", f"source {SCRIPT}; ec2_ami_lookup"],
        capture_output=True, text=True, env=env,
    )


def test_hit_prints_newest_available_id(tmp_path):
    # Listed out of date order, with a newer one still pending: the newest
    # *available* image wins, so neither list order nor a pending bake can
    # decide the answer.
    images = {"Images": [
        {"ImageId": "ami-newer", "CreationDate": "2026-02-01T00:00:00.000Z", "State": "available"},
        {"ImageId": "ami-older", "CreationDate": "2026-01-01T00:00:00.000Z", "State": "available"},
        {"ImageId": "ami-pending", "CreationDate": "2026-03-01T00:00:00.000Z", "State": "pending"},
    ]}
    r = _run(tmp_path, json.dumps(images))
    assert r.returncode == 0 and r.stdout.strip() == "ami-newer"
    calls = (tmp_path / "calls").read_text()
    assert "tag:leerie:source-hash" in calls and "--query" not in calls


def test_empty_image_list_is_a_miss(tmp_path):
    r = _run(tmp_path, json.dumps({"Images": []}))
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_override_skips_aws(tmp_path):
    r = _run(tmp_path, "ami-x", {"LEERIE_EC2_AMI": "ami-override"})
    assert r.stdout.strip() == "ami-override"
    assert not (tmp_path / "calls").exists()
