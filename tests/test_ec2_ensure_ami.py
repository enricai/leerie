import os
import re
import signal
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "ec2-ami.sh"

AWS = r"""#!/bin/sh
echo "$@" >> "$T/calls"
case "$1 $2" in
  "ec2 describe-images")
    [ -f "$T/baked" ] && echo '{"Images":[{"ImageId":"ami-baked","State":"available","CreationDate":"2026-01-01T00:00:00Z"}]}' \
      || echo '{"Images":[]}' ;;
  "ssm get-parameter") echo '{"Parameter":{"Value":"ami-stock"}}' ;;
  "ec2 run-instances")
    # The instance exists (tagged) before the call returns its id; an
    # interrupt can land in that window.
    echo "$*" | sed -n 's/.*leerie:bake,Value=\([0-9a-f]*\).*/\1/p' > "$T/inflight"
    [ -n "$RUN_HANG" ] && sleep 30
    echo '{"Instances":[{"InstanceId":"i-bake"}]}' ;;
  "ec2 describe-instances")
    case "$*" in
      *tag:leerie:bake,Values=*)
        nonce="$(cat "$T/inflight" 2>/dev/null)"
        case "$*" in *"tag:leerie:bake,Values=$nonce "*|*"tag:leerie:bake,Values=$nonce") ;; *) nonce="" ;; esac
        [ -n "$nonce" ] && echo '{"Reservations":[{"Instances":[{"InstanceId":"i-inflight","State":{"Name":"pending"}}]}]}' \
          || echo '{"Reservations":[]}' ;;
      *)
        st=running; [ -f "$T/stopped" ] && st=stopped
        echo "{\"Reservations\":[{\"Instances\":[{\"InstanceId\":\"i-bake\",\"State\":{\"Name\":\"$st\"},\"PublicIpAddress\":\"1.2.3.4\"}]}]}" ;;
    esac ;;
  "ec2 stop-instances") touch "$T/stopped" ;;
  "ec2 create-image") echo '{"ImageId":"ami-baked"}'; touch "$T/baked" ;;
  "ec2 describe-subnets") echo '{"Subnets":[]}' ;;
esac
exit 0
"""
SSH = '#!/bin/sh\necho "$@" >> "$T/ssh_calls"\ncat >/dev/null 2>&1 || true\n[ -n "$SSH_FAIL" ] && [ "$*" != "${*%bash -s}" ] && exit 1\n[ -n "$SSH_HANG" ] && [ "$*" != "${*%bash -s}" ] && sleep 30\nexit 0\n'


def _ensure_ami_env(tmp_path, **extra):
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
                          capture_output=True, text=True, env=_ensure_ami_env(tmp_path, **extra))


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
                         env=_ensure_ami_env(tmp_path, SSH_HANG="1"), stdout=subprocess.PIPE,
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


def test_changed_hash_rebakes_with_new_hash_tag(tmp_path):
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "1.0.0"}')
    for d in ("orchestrator", "scripts", "prompts"):
        (root / d).mkdir()
    (root / "requirements.txt").write_text("")
    (root / "Dockerfile").write_text("one")
    _run(tmp_path, LEERIE_EC2_AMI_ROOT=str(root))
    first = (tmp_path / "calls").read_text()
    n = _calls(tmp_path).count("ec2 run-instances")
    assert n == 1
    # The stub treats any tag lookup as a hit once baked; drop that state so a
    # changed hash is judged on its own, as a real describe-images filter would.
    (tmp_path / "baked").unlink()
    (tmp_path / "stopped").unlink()
    (root / "Dockerfile").write_text("two")
    _run(tmp_path, LEERIE_EC2_AMI_ROOT=str(root))
    calls = (tmp_path / "calls").read_text()
    assert _calls(tmp_path).count("ec2 run-instances") == 2
    import re
    tagged = [re.search(r"ResourceType=image,Tags=.*?source-hash,Value=([0-9a-f]{64})", l).group(1)
              for l in calls.splitlines() if l.startswith("ec2 create-image")]
    assert len(tagged) == 2 and tagged[0] != tagged[1]


def test_bake_instance_gets_public_ip_nonce_tag_and_identity(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    run = next(l for l in (tmp_path / "calls").read_text().splitlines() if l.startswith("ec2 run-instances"))
    # The subnet override (subnet-1) need not auto-assign: the public IP is asked for.
    assert "--network-interfaces AssociatePublicIpAddress=true,DeviceIndex=0,SubnetId=subnet-1,Groups=sg-1" in run
    assert "--subnet-id" not in run and "--security-group-ids" not in run
    assert re.search(r"Key=leerie:managed,Value=true\},\{Key=leerie:bake,Value=[0-9a-f]{32}\}", run)
    ssh = (tmp_path / "ssh_calls").read_text().splitlines()
    assert ssh and all(f"-i {tmp_path / 'k.pem'}" in l for l in ssh)
    # The bake script learns the region (regional SSM-agent bucket).
    assert any("LEERIE_BAKE_REGION=us-east-1 bash -s" in l for l in ssh)


def test_sigint_while_run_instances_in_flight_terminates_by_nonce(tmp_path):
    p = subprocess.Popen(["bash", "-c", f"source {SCRIPT}; ensure_ami"],
                         env=_ensure_ami_env(tmp_path, RUN_HANG="1"), stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, start_new_session=True)
    for _ in range(100):
        time.sleep(0.1)
        if (tmp_path / "inflight").exists() and (tmp_path / "inflight").read_text().strip():
            break
    time.sleep(0.5)
    os.killpg(p.pid, signal.SIGINT)
    p.wait(timeout=40)
    calls = (tmp_path / "calls").read_text().splitlines()
    nonce = (tmp_path / "inflight").read_text().strip()
    lookup = [l for l in calls if l.startswith("ec2 describe-instances") and "tag:leerie:bake" in l]
    assert lookup and f"Values={nonce}" in lookup[0]
    term = [l for l in calls if l.startswith("ec2 terminate-instances")]
    assert term and "i-inflight" in term[0]
    assert not any(l.startswith("ec2 create-image") for l in calls)
