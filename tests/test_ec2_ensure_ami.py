import os
import signal
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "ec2-ami.sh"

AWS = r"""#!/bin/sh
echo "$@" >> "$T/calls"
case "$1 $2" in
  "ec2 describe-images") [ -f "$T/baked" ] && echo ami-baked || echo None ;;
  "ssm get-parameter") echo ami-stock ;;
  "ec2 run-instances") echo i-bake ;;
  "ec2 describe-instances")
    case "$*" in *PublicIp*) echo 1.2.3.4 ;; *) [ -f "$T/stopped" ] && echo stopped || echo running ;; esac ;;
  "ec2 stop-instances") touch "$T/stopped" ;;
  "ec2 create-image") echo ami-baked; touch "$T/baked" ;;
  "ec2 describe-subnets") echo '{"Subnets":[]}' ;;
esac
exit 0
"""
SSH = '#!/bin/sh\ncat >/dev/null 2>&1 || true\n[ -n "$SSH_FAIL" ] && [ "$*" != "${*%bash -s}" ] && exit 1\n[ -n "$SSH_HANG" ] && [ "$*" != "${*%bash -s}" ] && sleep 30\nexit 0\n'


def _env(tmp_path, **extra):
    b = tmp_path / "bin"
    b.mkdir(exist_ok=True)
    for name, body in (("aws", AWS), ("ssh", SSH)):
        (b / name).write_text(body)
        (b / name).chmod(0o755)
    (tmp_path / "k.pem").write_text("x")
    env = {**os.environ, "PATH": f"{b}:{os.environ['PATH']}", "T": str(tmp_path),
           "LEERIE_EC2_SUBNET_ID": "subnet-1", "LEERIE_EC2_SECURITY_GROUP": "sg-1",
           "LEERIE_EC2_KEY_NAME": "k", "LEERIE_EC2_SSH_KEY_FILE": str(tmp_path / "k.pem"),
           "LEERIE_EC2_BAKE_POLL_S": "0", "AWS_REGION": "us-east-1"}
    env.pop("LEERIE_EC2_AMI", None)
    env.update(extra)
    return env


def _calls(tmp_path):
    p = tmp_path / "calls"
    return [" ".join(l.split()[:2]) for l in p.read_text().splitlines()] if p.exists() else []


def _run(tmp_path, **extra):
    return subprocess.run(["bash", "-c", f"source {SCRIPT}; ensure_ami"],
                          capture_output=True, text=True, env=_env(tmp_path, **extra))


def test_miss_bakes_in_order_then_hit(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == "ami-baked"
    c = _calls(tmp_path)
    i = [c.index(f"ec2 {x}") for x in ("run-instances", "create-image", "terminate-instances")]
    assert i == sorted(i)
    assert "leerie:source-hash" in (tmp_path / "calls").read_text()
    n = len(c)
    r2 = _run(tmp_path)
    assert r2.stdout.strip() == "ami-baked"
    assert "ec2 run-instances" not in _calls(tmp_path)[n:]


def test_payload_failure_terminates_and_creates_no_image(tmp_path):
    r = _run(tmp_path, SSH_FAIL="1")
    assert r.returncode != 0
    c = _calls(tmp_path)
    assert "ec2 terminate-instances" in c and "ec2 create-image" not in c


def test_sigint_terminates(tmp_path):
    p = subprocess.Popen(["bash", "-c", f"source {SCRIPT}; ensure_ami"],
                         env=_env(tmp_path, SSH_HANG="1"), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True)
    for _ in range(100):
        time.sleep(0.1)
        if "ec2 run-instances" in _calls(tmp_path):
            break
    time.sleep(1)
    os.killpg(p.pid, signal.SIGINT)
    p.wait(timeout=20)
    c = _calls(tmp_path)
    assert "ec2 terminate-instances" in c and "ec2 create-image" not in c


def test_override_makes_no_aws_call(tmp_path):
    r = _run(tmp_path, LEERIE_EC2_AMI="ami-x")
    assert r.stdout.strip() == "ami-x" and not (tmp_path / "calls").exists()
