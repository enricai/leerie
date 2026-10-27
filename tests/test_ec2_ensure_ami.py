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
    case "$*" in
      *--image-ids*)
        # The post-create poll: pending for PENDING_POLLS calls, then the
        # IMAGE_END state (default available).
        n=$(( $(cat "$T/polls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$T/polls"
        if [ "$n" -le "${PENDING_POLLS:-0}" ]; then st=pending; else st="${IMAGE_END:-available}"; fi
        echo "{\"Images\":[{\"ImageId\":\"ami-baked\",\"State\":\"$st\"}]}" ;;
      *)
        [ -f "$T/baked" ] && echo '{"Images":[{"ImageId":"ami-baked","State":"available","CreationDate":"2026-01-01T00:00:00Z"}]}' \
          || echo '{"Images":[]}' ;;
    esac ;;
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
# Records argv and how many stdin bytes each call received; the bake-script
# run is the call naming ec2-bake.sh, which SSH_FAIL / SSH_HANG target.
SSH = (
    '#!/bin/sh\n'
    'echo "$@" >> "$T/ssh_calls"\n'
    'n=$(wc -c | tr -d " "); echo "$n" >> "$T/ssh_stdin_bytes"\n'
    # The real bake prints apt/pip output on stdout; so does this stub.
    'echo "Get:1 http://deb.debian.org stub-noise"\n'
    'case "$*" in *ec2-bake.sh*) [ -n "$SSH_FAIL" ] && exit 1; [ -n "$SSH_HANG" ] && sleep 30 ;; esac\n'
    'exit 0\n'
)


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
    # Exactly the id: the caller does ami="$(ensure_ami)", so any bake
    # output on stdout becomes the --image-id (a real run hit this).
    assert r.stdout == "ami-baked\n"
    assert "stub-noise" in r.stderr
    c = _calls(tmp_path)
    i = [c.index(f"ec2 {x}") for x in ("run-instances", "create-image", "terminate-instances")]
    assert i == sorted(i)
    assert "leerie:source-hash" in (tmp_path / "calls").read_text()
    n = len(c)
    r2 = _run(tmp_path)
    assert r2.stdout == "ami-baked\n"
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
    assert any("LEERIE_BAKE_REGION=us-east-1 bash /tmp/leerie-bake/scripts/remote/ec2-bake.sh" in l for l in ssh)


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


def _ssh_calls(tmp_path):
    return (tmp_path / "ssh_calls").read_text().splitlines()


SHIP_TAIL = "sudo -n sh -c 'mkdir -p '\\''/tmp/leerie-bake'\\'' && tar -xzC '\\''/tmp/leerie-bake'\\'''"


def test_bake_ships_via_tar_pipe_and_runs_the_script_by_path_with_stdin_closed(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    calls = _ssh_calls(tmp_path)
    stdin = (tmp_path / "ssh_stdin_bytes").read_text().split()
    ship = [i for i, l in enumerate(calls) if l.endswith(SHIP_TAIL)]
    run = [i for i, l in enumerate(calls) if "bash /tmp/leerie-bake/scripts/remote/ec2-bake.sh" in l]
    assert ship and run and ship[0] < run[0]
    assert int(stdin[ship[0]]) > 0          # the gzip tar went over the pipe
    assert int(stdin[run[0]]) == 0          # the script run reads nothing on stdin
    assert not any(l.endswith("bash -s") for l in calls)


def test_bake_ssh_uses_the_bake_instances_own_known_hosts(tmp_path):
    r = _run(tmp_path, HOME=str(tmp_path / "home"))
    assert r.returncode == 0, r.stderr
    kh = f"UserKnownHostsFile={tmp_path / 'home'}/.leerie/.ec2-keys/known_hosts/i-bake"
    calls = _ssh_calls(tmp_path)
    assert calls and all(kh in l for l in calls)
    assert not any("StrictHostKeyChecking=no" in l or "/dev/null" in l for l in calls)


def test_image_wait_outlasts_pending_polls(tmp_path):
    # More pending polls than the CLI waiter's 40 attempts; the bounded poll
    # keeps going and the bake succeeds.
    r = _run(tmp_path, PENDING_POLLS="45", LEERIE_EC2_AMI_WAIT_S="1000")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "ami-baked\n"
    assert int((tmp_path / "polls").read_text()) == 46
    assert "ec2 deregister-image" not in _calls(tmp_path)
    assert "ec2 wait" not in _calls(tmp_path)


def test_failed_image_is_deregistered_and_the_bake_fails(tmp_path):
    r = _run(tmp_path, IMAGE_END="failed")
    assert r.returncode != 0
    assert "ec2 deregister-image" in _calls(tmp_path)
    assert "ec2 terminate-instances" in _calls(tmp_path)
    assert int((tmp_path / "polls").read_text()) == 1   # terminal state ends the wait at once


def test_image_wait_budget_is_bounded(tmp_path):
    r = _run(tmp_path, PENDING_POLLS="1000", LEERIE_EC2_AMI_WAIT_S="3")
    assert r.returncode != 0
    # step 0 under test counts 1s per wait: polls at 0,1,2,3 waited seconds.
    assert int((tmp_path / "polls").read_text()) == 4
    assert "LEERIE_EC2_AMI_WAIT_S" in r.stderr
    assert "ec2 deregister-image" in _calls(tmp_path)


def test_bake_never_runs_the_callers_exit_trap(tmp_path):
    # The launcher holds `trap 'rm -rf "$STAGE"' EXIT` and calls
    # ami="$(ensure_ami)". A bake that re-installs the caller's trap inside
    # that subshell runs it on the subshell's exit — on a real run that
    # deleted the staged credentials before seeding.
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / ".credentials.json").write_text("{}")
    script = (
        f"source {SCRIPT}; "
        f"trap 'rm -rf {stage}' EXIT; "
        'ami="$(ensure_ami)"; rc=$?; '
        f'[ -d {stage} ] && echo "stage-intact ami=$ami rc=$rc" || echo "stage-deleted"; '
        "trap - EXIT"
    )
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env=_ensure_ami_env(tmp_path))
    assert r.stdout.strip() == "stage-intact ami=ami-baked rc=0", r.stderr
    assert (stage / ".credentials.json").exists()
