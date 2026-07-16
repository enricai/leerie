"""Tests for the `leerie` launcher's `--stop` verb EC2 dispatch.

Before this file, `--stop` validated `--runtime` against only
`fly`/`local` (DESIGN §6 *The user-visible verb surface*;
IMPLEMENTATION.md "Explicit pause and destroy verbs") and an EC2 run
fell through to the "not a live Fly machine or local container" error
and exited 1, leaving the instance running (and billing). This file
pins the EC2 counterpart: `--stop <run-id>` on a run dir carrying an
`ec2-instance.json` sidecar (written unconditionally by
`ec2-provision.sh`'s `provision_instance()` — DESIGN §6 "Run
identifier") auto-detects the EC2 runtime, resolves the instance id
from the sidecar, calls `stop_instance()` (`scripts/remote/
ec2-provision.sh`), and records `paused_at`/`pause_reason`/
`ec2_instance_id` on `run.json` the way the Fly path already does for
`fly_machine_id`.

Harness: invokes the real `leerie` launcher binary (not an extracted
block — `--stop` is an early fast-path verb dispatched before any
container preflight, unlike `RUNTIME=ec2`'s deep dispatch block that
`tests/test_ec2_e2e_provision.py` must extract) against
`tests/ec2_stub.py`'s resource-tracking `aws` stub, mirroring
`tests/test_chain_launcher_id_dispatch.py`'s subprocess-invocation
pattern for `LEERIE_STATE_HOST_DIR`/`USER_REPO` env setup and
`tests/test_ec2_e2e_provision.py`'s `stub_aws_env`-style minimal
env-var AWS credential fixture (so `require_aws`'s `sts
get-caller-identity` probe succeeds without touching a real AWS
account).

Footgun reminder (CLAUDE.md): the launcher's state-dir override is
`LEERIE_STATE_HOST_DIR`, NOT `LEERIE_STATE_HOST_DIR`'s sibling
`LEERIE_STATE_DIR` alone — both are set here (mirroring
`test_chain_launcher_id_dispatch.py`) since the launcher derives
`LEERIE_STATE_HOST_DIR` from `LEERIE_STATE_DIR` early but some code
paths read `LEERIE_STATE_HOST_DIR` directly once exported.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

RUN_ID = "ec2-run-0001"


def _seed_running_instance(aws_dir: Path) -> str:
    """Directly seed a `running` instance in the stub's state.json,
    bypassing `run-instances` — this file only exercises the launcher's
    `--stop` dispatch, not provisioning (covered by
    tests/test_ec2_provision.py)."""
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {
        "state": "running",
        "public_ip": "203.0.113.20",
    }
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def _write_ec2_sidecar(run_dir: Path, run_id: str, instance_id: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": instance_id,
        "region": "us-east-1",
        "started_at": "2026-07-01T00:00:00+00:00",
        "run_id": run_id,
        "launcher_pid": 12345,
    }))
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
        "ec2_instance_id": instance_id,
    }))


def _env(tmp_path: Path, aws_dir: Path, *, home: Path | None = None) -> dict:
    state_dir = tmp_path / ".leerie" / "myrepo"
    if home is None:
        home = tmp_path / "home"
        home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(home),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "AWS_ACCESS_KEY_ID": "AKIASTUBFIXTURE",
        "AWS_SECRET_ACCESS_KEY": "stubfixturesecret",
        "AWS_REGION": "us-east-1",
    }
    return env, state_dir


def _run(tmp_path: Path, args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )


def test_stop_ec2_run_stops_instance_and_writes_sidecar(tmp_path: Path) -> None:
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)

    result = _run(tmp_path, ["--stop", RUN_ID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    # The instance is stopped, not terminated.
    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "stopped"

    run_json = json.loads((run_dir / "run.json").read_text())
    assert run_json.get("paused_at")
    assert run_json.get("pause_reason") == "user-requested"
    assert run_json.get("ec2_instance_id") == iid
    # killed_at must never be set by --stop.
    assert "killed_at" not in run_json or not run_json["killed_at"]


def test_stop_ec2_run_explicit_runtime_flag(tmp_path: Path) -> None:
    """--stop <run-id> --runtime ec2 (explicit, no autodetect) still works."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)

    result = _run(tmp_path, ["--stop", RUN_ID, "--runtime", "ec2"], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "stopped"


def test_stop_ec2_run_does_not_terminate_instance(tmp_path: Path) -> None:
    """--stop must never call terminate-instances — resumable, not destroyed."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)

    result = _run(tmp_path, ["--stop", RUN_ID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    from tests.ec2_stub import read_log
    log = read_log(aws_dir)
    assert not any("terminate-instances" in line for line in log)
    assert any("stop-instances" in line for line in log)


def test_stop_local_path_unchanged(tmp_path: Path) -> None:
    """A local run (no fly-machine.json, no ec2-instance.json, no live
    nerdctl container) still hits the pre-existing 'not a live ... or
    local container' error — the fly/local fallthrough path is
    unchanged by the EC2 branch addition."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
    run_id = "local-run-0001"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
    }))
    # No nerdctl on PATH — the local-container probe fails closed, and
    # with no fly-machine.json / ec2-instance.json sidecar the run falls
    # through to the existing error path.
    result = _run(tmp_path, ["--stop", run_id], env)
    assert result.returncode == 1
    assert "not a live Fly machine, local container, or EC2 instance" in result.stderr


def test_stop_runtime_flag_rejects_unknown_value(tmp_path: Path) -> None:
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    env, _ = _env(tmp_path, aws_dir)
    result = _run(tmp_path, ["--stop", "some-run", "--runtime", "bogus"], env)
    assert result.returncode == 1
    assert "must be 'local', 'fly', or 'ec2'" in result.stderr


def test_stop_ec2_run_missing_instance_id_fails_closed(tmp_path: Path) -> None:
    """An ec2-instance.json sidecar present but missing ec2_instance_id
    (shouldn't happen in practice, since provision_instance() writes it
    unconditionally, but the resolver must not silently no-op) fails
    with an actionable error rather than silently no-op'ing."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
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

    result = _run(tmp_path, ["--stop", RUN_ID], env)
    assert result.returncode == 1
    assert "no ec2_instance_id found" in result.stderr


def test_stop_ec2_run_credential_failure_does_not_call_stop_instances(tmp_path: Path) -> None:
    """A failing credential probe must abort before any aws ec2 call —
    same fail-closed discipline test_ec2_e2e_provision.py pins for the
    RUNTIME=ec2 dispatch branch."""
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)
    # No $HOME/.aws and no AWS_* env vars → resolve_aws_credentials fails
    # closed before require_aws's own probe is ever reached.
    env.pop("AWS_ACCESS_KEY_ID", None)
    env.pop("AWS_SECRET_ACCESS_KEY", None)
    env.pop("AWS_REGION", None)
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    env["HOME"] = str(empty_home)

    result = _run(tmp_path, ["--stop", RUN_ID], env)
    assert result.returncode == 1

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"
    from tests.ec2_stub import read_log
    log = read_log(aws_dir)
    assert not any("stop-instances" in line for line in log)
