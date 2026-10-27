"""Tests for `accept-blocked` and `list` recognizing EC2 runs.

Before this file, `accept-blocked` validated `--runtime` against only
`fly`/`local` (leerie:1321 pre-fix) and defaulted anything non-fly to
`local` (leerie:1330 pre-fix), so an EC2 run's `ec2-instance.json`
sidecar was silently ignored and the mutation was attempted against a
host-side `state.json` that (unlike the Fly path, where the volume is
the source of truth) does not exist for an EC2 run until
`fetch_state_ec2` streams it back at teardown (`scripts/remote/
ec2-fetch-branch.sh`). `list`'s runtime-aware view keyed only on
`fly-machine.json`/`LEERIE_FLY_APP` (leerie:1191 pre-fix), so `list
--runtime ec2` rendered every run unfiltered instead of restricting to
EC2 runs, and the underlying row-collection (`_collect_run_rows` in
`orchestrator/leerie.py`) tracked no EC2 axis at all.

This file pins the fix: `accept-blocked` now auto-detects EC2 the
same way `stop` already does (`_auto_detect_run_runtime`), accepts an
explicit `--runtime ec2`, and — mirroring the Fly path's wake-mutate-
pause dance — wakes a stopped instance, mutates state.json over SSM
(`ec2_remote_exec`, DESIGN §6 "Transport substitution for `flyctl ssh
console`"), mirrors the mutation onto the host copy if one exists, and
re-pauses the instance only if this verb woke it. `_collect_run_rows`/
`_list_runs` in `orchestrator/leerie.py` now track an `is_ec2` axis
(`ec2_instance_id` in run.json or `ec2-instance.json` present) so
`--runtime ec2` filters correctly and `--runtime local` excludes both
Fly and EC2 runs.

Harness: the `accept-blocked` tests invoke the real `leerie` launcher
binary against `tests/ec2_stub.py`'s stateful `aws` stub plus an `ssh`
stub that plays the instance for `ec2_remote_exec`: it runs the remote
`sudo -n bash -c <cmd>` locally with the invoking process's stdin
attached — the path `accept-blocked`'s EC2 branch relies on to pipe the
multi-line state-mutation Python program to the remote `python3 -`
(mirroring `flyctl ssh console -C "python3 -"` on the Fly path).
`list` tests exercise `orchestrator/leerie.py`'s `_list_runs()`
directly (no launcher subprocess, no AWS stub — pure filesystem
fixtures), mirroring `tests/test_list_runs.py`'s pattern.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.ec2_stub import (
    _STUB_SOURCE,
    build_ec2_launcher_env,
    read_log,
    read_state,
    run_launcher as _run_launcher_shared,
    seed_running_instance,
    write_ec2_sidecar,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

RUN_ID = "ec2-run-ab01"
SID = "feat-001"


# --- aws stub (stateful EC2 tracking) + ssh stub (remote exec) -------------

# ec2_remote_exec runs `ssh … <target> "sudo -n bash -c <cmd>"` (DESIGN §6:
# every non-interactive EC2 command goes over SSH). This stub plays the
# instance: it logs into the shared aws.log (one timeline with the EC2
# lifecycle calls), drops the instance-side `sudo -n`, points /work at the
# test fixture (the host's real /work is the live coordination mount here),
# and runs the command with its own stdin attached, returning its real exit
# status — as sshd does.
_SSH_STUB_SOURCE = r'''#!/usr/bin/env python3
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
with open(HERE / "aws.log", "a") as f:
    f.write("ssh " + " ".join(sys.argv[1:]) + "\n")
remote = sys.argv[-1]
if remote.startswith("sudo -n "):
    remote = remote[len("sudo -n "):]
dest = os.environ.get("LEERIE_TEST_WORK_DEST")
if dest:
    remote = remote.replace("/work", dest)
sys.exit(subprocess.run(["bash", "-c", remote], stdin=sys.stdin).returncode)
'''


def _stub_aws_with_ssm(dir: Path) -> Path:
    """Stateful EC2 `aws` stub (tests/ec2_stub.py) plus, beside it, the
    `ssh` stub above that plays the instance for ec2_remote_exec. Both
    write the same aws.log, so call order across them is assertable."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    stub = dir / "aws"
    stub.write_text(_STUB_SOURCE)
    stub.chmod(0o755)
    ssh = dir / "ssh"
    ssh.write_text(_SSH_STUB_SOURCE)
    ssh.chmod(0o755)
    (dir / "aws.log").write_text("")
    (dir / "state.json").write_text(json.dumps({"instances": {}, "volumes": {}}))
    return stub


def _seed_stopped_instance(aws_dir: Path) -> str:
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {"state": "stopped", "public_ip": "203.0.113.20", "status_ok": True}
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def _seed_remote_state(work_dest: Path, run_id: str, state: dict) -> Path:
    """Seed state.json at the rewritten '/work' fixture path — this is
    what accept-blocked's EC2 branch actually mutates over SSH (the
    genuine remote copy; the run dir's *host-side* state.json, if any,
    is a best-effort mirror written afterward)."""
    remote_dir = work_dest / ".leerie" / "runs" / run_id
    remote_dir.mkdir(parents=True, exist_ok=True)
    path = remote_dir / "state.json"
    path.write_text(json.dumps(state))
    return path


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return _run_launcher_shared(args, env, launcher=LAUNCHER, use_bash=True, timeout=30)


# --- accept-blocked: EC2 runtime resolution ------------------------------

def test_accept_blocked_ec2_autodetects_runtime_not_local(tmp_path: Path) -> None:
    """Control: an EC2 sidecar-bearing run must not silently resolve to
    'local' — the pre-fix launcher defaulted any non-fly runtime to
    'local' (leerie:1330 pre-fix), which would then look for a
    host-side state.json that doesn't exist for a live/paused EC2 run
    and fail with a 'local' error message rather than an EC2 one."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = seed_running_instance(aws_dir)

    env, state_dir, work_dest = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    # The pre-fix 'local' path's error text ("no state.json at
    # <state_dir>/state.json") must never appear — that would mean the
    # EC2 sidecar was ignored and the local branch ran instead.
    assert "no state.json at" not in result.stderr


def test_accept_blocked_ec2_writes_accept_record(tmp_path: Path) -> None:
    """The mutation actually lands on the remote (instance-side) copy:
    subtask_status[sid] flips to 'complete' and the sid is dropped from
    'blocked'. The host-side run dir's state.json (created here to
    prove the mirror step runs) reflects the same mutation afterward."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = seed_running_instance(aws_dir)

    env, state_dir, work_dest = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    write_ec2_sidecar(run_dir, RUN_ID, iid,
                        with_host_state={"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})
    remote_state_path = _seed_remote_state(
        work_dest, RUN_ID, {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    remote = json.loads(remote_state_path.read_text())
    assert remote["subtask_status"][SID] == "complete"
    assert SID not in (remote.get("blocked") or {})

    mirrored = json.loads((run_dir / "state.json").read_text())
    assert mirrored["subtask_status"][SID] == "complete"
    assert SID not in (mirrored.get("blocked") or {})


def test_accept_blocked_ec2_explicit_runtime_flag_accepted(tmp_path: Path) -> None:
    """`--runtime ec2` must be accepted by the validator, not rejected
    by the pre-fix fly|local-only check (leerie:1321 pre-fix)."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = seed_running_instance(aws_dir)

    env, state_dir, work_dest = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["accept-blocked", RUN_ID, SID, "--runtime", "ec2"], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "must be 'local', 'fly', or 'ec2'" not in result.stderr


def test_accept_blocked_rejects_unknown_runtime_value(tmp_path: Path) -> None:
    """Control: an actually-bogus --runtime value is still rejected —
    the fix widens the allowlist to fly|local|ec2, it doesn't remove
    validation entirely."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    env, _, _ = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    result = _run(["accept-blocked", "some-run", "some-sid", "--runtime", "bogus"], env)
    assert result.returncode == 1
    assert "must be 'local', 'fly', or 'ec2'" in result.stderr


def test_accept_blocked_ec2_wakes_stopped_instance_and_repauses(tmp_path: Path) -> None:
    """A paused (stopped) EC2 instance is woken to mutate state, then
    paused again afterward — the same conditional-teardown discipline
    the Fly path already has (only re-pause if THIS verb woke it)."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = _seed_stopped_instance(aws_dir)

    env, state_dir, work_dest = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "stopped"
    log = read_log(aws_dir)
    assert any("start-instances" in line for line in log)
    assert any("stop-instances" in line for line in log)
    assert not any("terminate-instances" in line for line in log)

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("paused_at")
    assert run_json.get("pause_reason") == "user-requested"


def test_accept_blocked_ec2_already_running_not_paused_afterward(tmp_path: Path) -> None:
    """An already-running instance (e.g. accepting a block on a live
    run) must not be paused afterward — mirrors the Fly path's
    _ab_started_machine conditional."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = seed_running_instance(aws_dir)

    env, state_dir, work_dest = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"
    log = read_log(aws_dir)
    assert not any("start-instances" in line for line in log)
    assert not any("stop-instances" in line for line in log)


def test_accept_blocked_ec2_missing_instance_id_fails_closed(tmp_path: Path) -> None:
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)

    env, state_dir, _ = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_dir = state_dir / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": None,
        "region": "us-east-1",
        "started_at": "2026-07-01T00:00:00+00:00",
        "run_id": RUN_ID,
        "launcher_pid": 12345,
    }))
    (run_dir / "run.json").write_text(json.dumps({"run_id": RUN_ID}))

    result = _run(["accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 1
    assert "no ec2_instance_id found" in result.stderr


def test_accept_blocked_local_path_unchanged(tmp_path: Path) -> None:
    """A run with no fly-machine.json / ec2-instance.json sidecar still
    takes the local path unchanged."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    env, state_dir, _ = build_ec2_launcher_env(tmp_path, aws_dir, with_work_dest=True)
    run_id = "local-run-ab01"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"subtask_status": {SID: "failed"}}))

    result = _run(["accept-blocked", run_id, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    mutated = json.loads((run_dir / "state.json").read_text())
    assert mutated["subtask_status"][SID] == "complete"
    # No AWS call must have been made for a local run.
    log = read_log(aws_dir)
    assert log == []


# --- list: EC2 runtime recognition (Python layer) ------------------------

def _make_run(root: Path, run_id: str, state: dict,
              run_json: dict | None = None,
              ec2_sidecar: dict | None = None) -> None:
    rd = root / "runs" / run_id
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "state.json").write_text(json.dumps(state))
    if run_json is not None:
        (rd / "run.json").write_text(json.dumps(run_json))
    if ec2_sidecar is not None:
        (rd / "ec2-instance.json").write_text(json.dumps(ec2_sidecar))


def test_list_ec2_run_renders_populated_status_without_fly_app(leerie, tmp_path, capsys, monkeypatch):
    """Control: an EC2 run must render with a populated status column
    via plain list, with no LEERIE_FLY_APP set at all — the pre-fix
    list --runtime fly arm required LEERIE_FLY_APP and keyed on
    fly-machine.json, so an EC2 run's runtime-aware view rendered empty
    columns; the runtime-agnostic default table must not depend on it
    either."""
    monkeypatch.delenv("LEERIE_FLY_APP", raising=False)
    _make_run(
        tmp_path, RUN_ID,
        {"started_at": "2026-07-01T00:00:00+00:00", "task": "x"},
        run_json={"ec2_instance_id": "i-0000000000000000a",
                  "branch": f"leerie/runs/{RUN_ID}"},
        ec2_sidecar={"ec2_instance_id": "i-0000000000000000a"},
    )
    leerie._list_runs(tmp_path)
    out = capsys.readouterr().out
    assert RUN_ID in out
    assert "in-progress" in out
    # No empty-column placeholder ("—") should be needed for status —
    # only cost may legitimately render as "—" for a run with no telemetry.
    for line in out.splitlines():
        if RUN_ID in line:
            assert "in-progress" in line


def test_list_runtime_ec2_filters_to_ec2_runs_only(leerie, tmp_path, capsys):
    """Control: list --runtime ec2 must restrict to EC2 runs — the
    pre-fix filter recognized only 'fly'/'local' and silently returned
    every row unfiltered for any other value, including 'ec2'."""
    _make_run(tmp_path, "fly-run-aaaaaa",
              {"started_at": "2026-07-01T00:00:00+00:00", "task": "x"},
              run_json={"fly_machine_id": "3d8d996ce70e18"})
    _make_run(tmp_path, "local-run-bbbbbb",
              {"started_at": "2026-07-01T01:00:00+00:00", "task": "y"})
    _make_run(tmp_path, RUN_ID,
              {"started_at": "2026-07-01T02:00:00+00:00", "task": "z"},
              run_json={"ec2_instance_id": "i-0000000000000000a"},
              ec2_sidecar={"ec2_instance_id": "i-0000000000000000a"})

    leerie._list_runs(tmp_path, runtime_filter="ec2")
    out = capsys.readouterr().out
    assert RUN_ID in out
    assert "fly-run-aaaaaa" not in out
    assert "local-run-bbbbbb" not in out


def test_list_runtime_local_excludes_ec2_runs(leerie, tmp_path, capsys):
    """list --runtime local must exclude EC2 runs too, not just Fly
    ones — the pre-fix 'local' filter was defined as 'not fly', which
    would have wrongly included EC2 runs."""
    _make_run(tmp_path, "local-run-cccccc",
              {"started_at": "2026-07-01T00:00:00+00:00", "task": "x"})
    _make_run(tmp_path, RUN_ID,
              {"started_at": "2026-07-01T01:00:00+00:00", "task": "z"},
              run_json={"ec2_instance_id": "i-0000000000000000a"},
              ec2_sidecar={"ec2_instance_id": "i-0000000000000000a"})

    leerie._list_runs(tmp_path, runtime_filter="local")
    out = capsys.readouterr().out
    assert "local-run-cccccc" in out
    assert RUN_ID not in out


def test_list_ec2_run_detected_via_sidecar_only(leerie, tmp_path, capsys):
    """An EC2 run with only ec2-instance.json (no run.json yet, e.g. a
    seed-in-progress state) is still recognized as EC2 by --collect_run_rows'
    fallback probe (mirrors the Fly orphan-detection precedent)."""
    _make_run(tmp_path, RUN_ID,
              {"started_at": "2026-07-01T00:00:00+00:00", "task": "z"},
              ec2_sidecar={"ec2_instance_id": "i-0000000000000000a"})

    leerie._list_runs(tmp_path, runtime_filter="ec2")
    out = capsys.readouterr().out
    assert RUN_ID in out
