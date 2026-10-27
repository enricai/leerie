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


def test_hit_prints_id(tmp_path):
    r = _run(tmp_path, "ami-123")
    assert r.returncode == 0 and r.stdout.strip() == "ami-123"
    assert "tag:leerie:source-hash" in (tmp_path / "calls").read_text()


def test_none_is_a_miss(tmp_path):
    r = _run(tmp_path, "None")
    assert r.returncode == 0 and r.stdout.strip() == ""


def test_override_skips_aws(tmp_path):
    r = _run(tmp_path, "ami-x", {"LEERIE_EC2_AMI": "ami-override"})
    assert r.stdout.strip() == "ami-override"
    assert not (tmp_path / "calls").exists()
