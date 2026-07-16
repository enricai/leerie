"""Tests for `--accept-blocked` and `--list` recognizing EC2 runs.

Before this file, `--accept-blocked` validated `--runtime` against only
`fly`/`local` (leerie:1321 pre-fix) and defaulted anything non-fly to
`local` (leerie:1330 pre-fix), so an EC2 run's `ec2-instance.json`
sidecar was silently ignored and the mutation was attempted against a
host-side `state.json` that (unlike the Fly path, where the volume is
the source of truth) does not exist for an EC2 run until
`fetch_state_ec2` streams it back at teardown (`scripts/remote/
ec2-fetch-branch.sh`). `--list`'s runtime-aware view keyed only on
`fly-machine.json`/`LEERIE_FLY_APP` (leerie:1191 pre-fix), so `--list
--runtime ec2` rendered every run unfiltered instead of restricting to
EC2 runs, and the underlying row-collection (`_collect_run_rows` in
`orchestrator/leerie.py`) tracked no EC2 axis at all.

This file pins the fix: `--accept-blocked` now auto-detects EC2 the
same way `--stop` already does (`_auto_detect_run_runtime`), accepts an
explicit `--runtime ec2`, and — mirroring the Fly path's wake-mutate-
pause dance — wakes a stopped instance, mutates state.json over SSM
(`ec2_remote_exec`, DESIGN §6 "Transport substitution for `flyctl ssh
console`"), mirrors the mutation onto the host copy if one exists, and
re-pauses the instance only if this verb woke it. `_collect_run_rows`/
`list_runs` in `orchestrator/leerie.py` now track an `is_ec2` axis
(`ec2_instance_id` in run.json or `ec2-instance.json` present) so
`--runtime ec2` filters correctly and `--runtime local` excludes both
Fly and EC2 runs.

Harness: the `--accept-blocked` tests invoke the real `leerie` launcher
binary against a stubbed `aws` that composes `tests/ec2_stub.py`'s
stateful EC2 instance tracking with an `ssm start-session` handler
(absent from `ec2_stub.py` itself, which only tests's the argv-level
lifecycle) that decodes `ec2_remote_exec`'s base64-wrapped command and
executes it with the invoking process's stdin drained through — the
same mechanism `--accept-blocked`'s EC2 branch relies on to pipe the
multi-line state-mutation Python program to the remote `python3 -`
(mirroring `flyctl ssh console -C "python3 -"` on the Fly path).
`--list` tests exercise `orchestrator/leerie.py`'s `list_runs()`
directly (no launcher subprocess, no AWS stub — pure filesystem
fixtures), mirroring `tests/test_list_runs.py`'s pattern.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.ec2_stub import _STUB_SOURCE, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

RUN_ID = "ec2-run-ab01"
SID = "feat-001"


# --- aws stub: stateful EC2 tracking + SSM exec ----------------------------

_SSM_STUB_SOURCE = r'''#!/usr/bin/env python3
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _handle_ssm(argv):
    cmd = None
    for a in argv:
        if a.startswith("command="):
            # aws CLI syntax: --parameters command=["<cmd>"]
            raw = a[len("command="):]
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            if raw.startswith('"') and raw.endswith('"'):
                raw = raw[1:-1]
            cmd = raw
    if cmd is None:
        print("{}")
        return 0
    # ec2_remote_exec wraps its real command as a *local* shell pipeline
    # string: "echo <b64> | base64 -d | bash". A literal `bash -c cmd`
    # here would run that pipeline verbatim, but the final `bash` in a
    # shell pipe gets its stdin from the pipe, not from this stub
    # process's own stdin — so a naive reproduction can never forward an
    # interactive session's stdin to the wrapped command, unlike the
    # real SSM agent, which forwards the session's I/O directly to the
    # remote command it execs (no local shell pipe in between). Decode
    # the payload ourselves and exec it directly with this process's
    # stdin attached, modeling that real behavior rather than the
    # local-pipe artifact of the wrapper string.
    m = re.search(r"echo (\S+) \| base64 -d \| bash", cmd)
    decoded = base64.b64decode(m.group(1)).decode() if m else cmd
    # The real remote path convention is /work/.leerie/runs/<id>/... (an
    # actual EC2 instance's mounted repo root). This stub never leaves
    # the test host, so /work would mean the *host's* real /work — the
    # live leerie coordination mount in this sandbox, not a scratch dir.
    # Rewrite it to a test-scoped fixture dir instead, mirroring
    # test_ec2_seed_repo.py's stubbed-ssh /work rewrite.
    dest = os.environ.get("LEERIE_TEST_WORK_DEST")
    if dest:
        decoded = decoded.replace("/work", dest)
    proc = subprocess.run(["bash", "-c", decoded], stdin=sys.stdin)
    return proc.returncode


def main(argv):
    with open(HERE / "aws.log", "a") as f:
        f.write(" ".join(argv) + "\n")
    if argv and argv[0] == "ssm" and len(argv) > 1 and argv[1] == "start-session":
        return _handle_ssm(argv)
    return None  # sentinel: fall through to the stateful EC2 stub


if __name__ == "__main__":
    rc = main(sys.argv[1:])
    if rc is not None:
        sys.exit(rc)
'''


def _stub_aws_with_ssm(dir: Path) -> Path:
    """Stateful EC2 `aws` stub (tests/ec2_stub.py) + an `ssm
    start-session` handler layered on top, since ec2_stub.py's own stub
    has no SSM support (it is exercised only via argv-level lifecycle
    tests elsewhere). Composes rather than forks: the SSM branch is
    tried first; anything else falls through to the stateful stub's
    `main()`, imported by relative path so both stubs share one
    `state.json`/`aws.log`."""
    dir = Path(dir)
    dir.mkdir(parents=True, exist_ok=True)
    combined = _SSM_STUB_SOURCE + "\n\n# --- stateful EC2 stub ---\n" + _STUB_SOURCE.split(
        "if __name__ == \"__main__\":", 1
    )[0] + (
        "\nif __name__ == \"__main__\":\n"
        "    _rc = main(sys.argv[1:])\n"
        "    if _rc is None:\n"
        "        _rc = _stateful_main(sys.argv[1:])\n"
        "    sys.exit(_rc)\n"
    )
    # The stateful stub defines its own `main`; rename it so the SSM
    # wrapper's `main` (which falls through by returning None) doesn't
    # collide with it.
    combined = combined.replace("def main(argv):\n    with open(LOG_PATH", "def _stateful_main(argv):\n    with open(LOG_PATH")
    stub = dir / "aws"
    stub.write_text(combined)
    stub.chmod(0o755)
    (dir / "aws.log").write_text("")
    (dir / "state.json").write_text(json.dumps({"instances": {}, "volumes": {}}))
    return stub


def _seed_running_instance(aws_dir: Path) -> str:
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {"state": "running", "public_ip": "203.0.113.20", "status_ok": True}
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def _seed_stopped_instance(aws_dir: Path) -> str:
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {"state": "stopped", "public_ip": "203.0.113.20", "status_ok": True}
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def _write_ec2_sidecar(run_dir: Path, run_id: str, instance_id: str,
                        with_host_state: dict | None = None) -> None:
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
    if with_host_state is not None:
        (run_dir / "state.json").write_text(json.dumps(with_host_state))


def _seed_remote_state(work_dest: Path, run_id: str, state: dict) -> Path:
    """Seed state.json at the rewritten '/work' fixture path — this is
    what --accept-blocked's EC2 branch actually mutates over SSM (the
    genuine remote copy; the run dir's *host-side* state.json, if any,
    is a best-effort mirror written afterward)."""
    remote_dir = work_dest / ".leerie" / "runs" / run_id
    remote_dir.mkdir(parents=True, exist_ok=True)
    path = remote_dir / "state.json"
    path.write_text(json.dumps(state))
    return path


def _env(tmp_path: Path, aws_dir: Path) -> tuple[dict, Path, Path]:
    state_dir = tmp_path / ".leerie" / "myrepo"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    work_dest = tmp_path / "remote-work"
    work_dest.mkdir(parents=True, exist_ok=True)
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
        "LEERIE_TEST_WORK_DEST": str(work_dest),
    }
    return env, state_dir, work_dest


def _run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )


# --- --accept-blocked: EC2 runtime resolution ------------------------------

def test_accept_blocked_ec2_autodetects_runtime_not_local(tmp_path: Path) -> None:
    """Control: an EC2 sidecar-bearing run must not silently resolve to
    'local' — the pre-fix launcher defaulted any non-fly runtime to
    'local' (leerie:1330 pre-fix), which would then look for a
    host-side state.json that doesn't exist for a live/paused EC2 run
    and fail with a 'local' error message rather than an EC2 one."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir, work_dest = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["--accept-blocked", RUN_ID, SID], env)
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
    iid = _seed_running_instance(aws_dir)

    env, state_dir, work_dest = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid,
                        with_host_state={"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})
    remote_state_path = _seed_remote_state(
        work_dest, RUN_ID, {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["--accept-blocked", RUN_ID, SID], env)
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
    iid = _seed_running_instance(aws_dir)

    env, state_dir, work_dest = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["--accept-blocked", RUN_ID, SID, "--runtime", "ec2"], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "must be 'local', 'fly', or 'ec2'" not in result.stderr


def test_accept_blocked_rejects_unknown_runtime_value(tmp_path: Path) -> None:
    """Control: an actually-bogus --runtime value is still rejected —
    the fix widens the allowlist to fly|local|ec2, it doesn't remove
    validation entirely."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    env, _, _ = _env(tmp_path, aws_dir)
    result = _run(["--accept-blocked", "some-run", "some-sid", "--runtime", "bogus"], env)
    assert result.returncode == 1
    assert "must be 'local', 'fly', or 'ec2'" in result.stderr


def test_accept_blocked_ec2_wakes_stopped_instance_and_repauses(tmp_path: Path) -> None:
    """A paused (stopped) EC2 instance is woken to mutate state, then
    paused again afterward — the same conditional-teardown discipline
    the Fly path already has (only re-pause if THIS verb woke it)."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    iid = _seed_stopped_instance(aws_dir)

    env, state_dir, work_dest = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["--accept-blocked", RUN_ID, SID], env)
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
    iid = _seed_running_instance(aws_dir)

    env, state_dir, work_dest = _env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)
    _seed_remote_state(work_dest, RUN_ID,
                        {"subtask_status": {SID: "blocked"}, "blocked": {SID: {}}})

    result = _run(["--accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"

    state = read_state(aws_dir)
    assert state["instances"][iid]["state"] == "running"
    log = read_log(aws_dir)
    assert not any("start-instances" in line for line in log)
    assert not any("stop-instances" in line for line in log)


def test_accept_blocked_ec2_missing_instance_id_fails_closed(tmp_path: Path) -> None:
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)

    env, state_dir, _ = _env(tmp_path, aws_dir)
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

    result = _run(["--accept-blocked", RUN_ID, SID], env)
    assert result.returncode == 1
    assert "no ec2_instance_id found" in result.stderr


def test_accept_blocked_local_path_unchanged(tmp_path: Path) -> None:
    """A run with no fly-machine.json / ec2-instance.json sidecar still
    takes the local path unchanged."""
    aws_dir = tmp_path / "bin"
    _stub_aws_with_ssm(aws_dir)
    env, state_dir, _ = _env(tmp_path, aws_dir)
    run_id = "local-run-ab01"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps(
        {"subtask_status": {SID: "failed"}}))

    result = _run(["--accept-blocked", run_id, SID], env)
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    mutated = json.loads((run_dir / "state.json").read_text())
    assert mutated["subtask_status"][SID] == "complete"
    # No AWS call must have been made for a local run.
    log = read_log(aws_dir)
    assert log == []


# --- --list: EC2 runtime recognition (Python layer) ------------------------

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
    via plain --list, with no LEERIE_FLY_APP set at all — the pre-fix
    --list --runtime fly arm required LEERIE_FLY_APP and keyed on
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
    leerie.list_runs(tmp_path)
    out = capsys.readouterr().out
    assert RUN_ID in out
    assert "in-progress" in out
    # No empty-column placeholder ("—") should be needed for status —
    # only cost may legitimately render as "—" for a run with no telemetry.
    for line in out.splitlines():
        if RUN_ID in line:
            assert "in-progress" in line


def test_list_runtime_ec2_filters_to_ec2_runs_only(leerie, tmp_path, capsys):
    """Control: --list --runtime ec2 must restrict to EC2 runs — the
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

    leerie.list_runs(tmp_path, runtime_filter="ec2")
    out = capsys.readouterr().out
    assert RUN_ID in out
    assert "fly-run-aaaaaa" not in out
    assert "local-run-bbbbbb" not in out


def test_list_runtime_local_excludes_ec2_runs(leerie, tmp_path, capsys):
    """--list --runtime local must exclude EC2 runs too, not just Fly
    ones — the pre-fix 'local' filter was defined as 'not fly', which
    would have wrongly included EC2 runs."""
    _make_run(tmp_path, "local-run-cccccc",
              {"started_at": "2026-07-01T00:00:00+00:00", "task": "x"})
    _make_run(tmp_path, RUN_ID,
              {"started_at": "2026-07-01T01:00:00+00:00", "task": "z"},
              run_json={"ec2_instance_id": "i-0000000000000000a"},
              ec2_sidecar={"ec2_instance_id": "i-0000000000000000a"})

    leerie.list_runs(tmp_path, runtime_filter="local")
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

    leerie.list_runs(tmp_path, runtime_filter="ec2")
    out = capsys.readouterr().out
    assert RUN_ID in out
