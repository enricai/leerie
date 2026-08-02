"""Tests for Phase 2 (pause-on-failure) bash surface.

Covers:
  - lib.sh:  update_run_json atomic merge
  - lib.sh:  iso_now timestamp
  - provision.sh: stop_machine idempotency + no-op when machine id empty
  - provision.sh: decide_teardown classification (rc → stop vs destroy)
  - provision.sh: decide_teardown writes paused_at + pause_reason + fly_machine_id
  - resume-machine.sh: resume_machine starts a stopped machine and clears paused_at
  - resume-machine.sh: resume_machine image-update on version drift (fail-open, state-host-dir)

All tests stub flyctl so no real Fly.io calls are made.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"
RESUME_SH = REPO_ROOT / "scripts" / "remote" / "resume-machine.sh"


def _run_bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _make_flyctl_stub(tmp_path: Path, *, behavior: str) -> Path:
    """Write a stub flyctl that records its argv to flyctl.log.

    behavior options:
      "happy"  — auth ok, machine run returns text "Machine ID: mach-001",
                 status returns JSON {state:started}, stop/destroy ok
      "stop_ok" — machine stop returns 0

    Note: `flyctl machine run` does NOT support --json (only certain
    other subcommands do, e.g. `flyctl machine status --json`). The
    real flyctl output is human-readable text containing a line like
    "Machine ID: <id>", which provision.sh parses via awk.
    `flyctl machine status` DOES accept --json.
    """
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "  'machine run') printf 'Success! A Machine has been launched\\n Machine ID: mach-001\\n State: created\\n'; exit 0 ;;\n"
        "  'machine status') printf 'Machine ID: mach-001\\nState: started\\n'; exit 0 ;;\n"
        "  'machine stop') exit 0 ;;\n"
        "  'machine destroy') exit 0 ;;\n"
        "  'machine start') exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return fake


# --- lib.sh ----------------------------------------------------------------

def test_update_run_json_creates_fields(tmp_path: Path):
    """update_run_json merges new fields into an existing sidecar."""
    sidecar = tmp_path / "run.json"
    sidecar.write_text(json.dumps({"run_id": "test-001", "branch": "leerie/runs/test-001"}))
    result = _run_bash(
        f"source {LIB_SH}; update_run_json {sidecar} fly_machine_id mach-abc paused_at 2026-05-29T16:00:00+00:00",
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(sidecar.read_text())
    assert data["fly_machine_id"] == "mach-abc"
    assert data["paused_at"] == "2026-05-29T16:00:00+00:00"
    assert data["run_id"] == "test-001"  # preserved
    assert data["branch"] == "leerie/runs/test-001"  # preserved


def test_update_run_json_empty_value_clears_to_null(tmp_path: Path):
    """An empty-string value clears the field to null (used by resume to wipe paused_at)."""
    sidecar = tmp_path / "run.json"
    sidecar.write_text(json.dumps({"paused_at": "2026-05-29T16:00:00", "pause_reason": "x"}))
    result = _run_bash(
        f'source {LIB_SH}; update_run_json {sidecar} paused_at "" pause_reason ""',
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(sidecar.read_text())
    assert data["paused_at"] is None
    assert data["pause_reason"] is None


def test_update_run_json_atomic_via_temp_rename(tmp_path: Path):
    """After successful merge, no temp files remain in the sidecar's directory."""
    sidecar = tmp_path / "run.json"
    sidecar.write_text("{}")
    _run_bash(f"source {LIB_SH}; update_run_json {sidecar} key value")
    leftover = [p.name for p in tmp_path.iterdir() if p.name != "run.json"]
    assert leftover == [], f"temp files leaked: {leftover}"


def test_iso_now_returns_iso8601(tmp_path: Path):
    """iso_now emits an ISO-8601 UTC timestamp parseable by Python."""
    result = _run_bash(f"source {LIB_SH}; iso_now")
    assert result.returncode == 0
    import datetime
    parsed = datetime.datetime.fromisoformat(result.stdout.strip())
    assert parsed.tzinfo is not None


# --- provision.sh: stop_machine -------------------------------------------

def test_stop_machine_noop_when_no_machine_id(tmp_path: Path):
    """stop_machine returns 0 when LEERIE_MACHINE_ID is empty (idempotency)."""
    result = _run_bash(
        f"source {PROVISION_SH}; LEERIE_MACHINE_ID=''; stop_machine; echo ok",
    )
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_stop_machine_calls_flyctl_stop(tmp_path: Path):
    """stop_machine invokes flyctl machine stop with the machine id."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    log = tmp_path / "flyctl.log"
    result = _run_bash(
        f"source {PROVISION_SH}; LEERIE_MACHINE_ID=mach-xyz; stop_machine",
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    invocations = log.read_text() if log.exists() else ""
    assert "machine stop mach-xyz" in invocations, invocations


# --- provision.sh: decide_teardown classification -------------------------

def _decide_teardown_with_rc(
    tmp_path: Path,
    rc: str,
    run_id: str = "test-run-001",
    fetch_branch_succeeds: bool = True,
    export_remote_run_id: bool = False,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run decide_teardown with LEERIE_REMOTE_EXIT_RC=$rc.

    Sets up:
      - stub flyctl that records argv
      - USER_REPO with a .leerie/runs/$run_id/run.json sidecar
      - LEERIE_MACHINE_ID=mach-test
      - On clean-exit branches (rc=0/10/75) decide_teardown calls
        `_try_fetch_branch_for_teardown` BEFORE destroy_machine.
        We override that helper to return either 0 (success — the
        host now has the work, destroy proceeds) or 1 (sync
        failed — machine stays running, sync_failed_at written).
        Tests that don't care about the fetch step set
        fetch_branch_succeeds=True (default).

    Returns (CompletedProcess, sidecar Path).
    """
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / run_id
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({"run_id": run_id, "branch": f"leerie/runs/{run_id}"}))
    fetch_rc = "0" if fetch_branch_succeeds else "1"
    script = (
        f"source {PROVISION_SH}; "
        # Override the fetch_branch helper. The real one sources
        # fetch-branch.sh and runs fetch_branch against the live
        # flyctl tunnel — both unstubbable here. Tests assert the
        # decide_teardown dispositions, not fetch_branch internals
        # (those are covered in test_fetch_branch_sh.py).
        f"_try_fetch_branch_for_teardown() {{ return {fetch_rc}; }}; "
        f"LEERIE_MACHINE_ID=mach-test; "
        f"decide_teardown"
    )
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "USER_REPO": str(user_repo),
        "LEERIE_RUN_ID": run_id,
        "LEERIE_REMOTE_EXIT_RC": rc,
    }
    if export_remote_run_id:
        # Needed when the test asserts on recovery-hint output: the
        # provision.sh hint branches at line 264 only when the
        # $USER_REPO/.leerie/runs/$LEERIE_REMOTE_RUN_ID dir resolves;
        # absent this var, run_dir is empty and the trap silently
        # destroys without printing the hint. Default off so the
        # existing rc=0/10/75/130/143/1/2 tests stay byte-identical.
        env["LEERIE_REMOTE_RUN_ID"] = run_id
    result = _run_bash(script, env=env)
    return result, sidecar


def test_decide_teardown_rc0_destroys(tmp_path: Path):
    """rc=0 (success) → destroy_machine (full reap)."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "0")
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine destroy mach-test" in invocations
    assert "machine stop mach-test" not in invocations
    data = json.loads(sidecar.read_text())
    assert data.get("paused_at") is None


def test_decide_teardown_rc10_destroys(tmp_path: Path):
    """rc=10 (EXIT_NEEDS_ANSWERS) → destroy."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "10")
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine destroy mach-test" in invocations


def test_decide_teardown_rc11_destroys(tmp_path: Path):
    """rc=11 (EXIT_BUDGET_INFEASIBLE, DESIGN §13 *Budget feasibility —
    fail fast at the cheapest moment*) → destroy, not pause.

    A budget-infeasible run is unrecoverable: --resume would die at
    `_run_phases`'s resume guard (no `waves` field), and the run made
    no commits to finalize. Routing rc=11 to the pause arm would leave
    the user paying for a Fly volume indefinitely. The correct
    disposition is the same as rc=0|10|75 (destroy after sync) and
    the recovery hint must be the budget-specific 're-run with
    --max-workers' variant, NOT the finalize hint that rc=10 uses."""
    result, sidecar = _decide_teardown_with_rc(
        tmp_path, "11", export_remote_run_id=True,
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine destroy mach-test" in invocations
    assert "machine stop mach-test" not in invocations
    data = json.loads(sidecar.read_text())
    assert data.get("paused_at") is None
    # Recovery hint coupling test: the rc=11 path must print the
    # budget-specific message, not the rc=10 finalize hint that
    # would mislead the user into running `leerie finalize` on a
    # run with nothing to push.
    assert "budget preflight rejected the plan" in result.stderr
    assert "re-run from the host with the recommended --max-workers" in result.stderr
    # Negative coupling: must NOT print the rc=10-style finalize hint.
    assert "to push and open a PR after the run completes" not in result.stderr


def test_decide_teardown_rc75_destroys(tmp_path: Path):
    """rc=75 (EX_TEMPFAIL, rate-limit) → destroy."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "75")
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine destroy mach-test" in invocations


def test_decide_teardown_rc0_sync_fails_keeps_machine_running(tmp_path: Path):
    """rc=0 with fetch_branch FAILURE → leave machine RUNNING; write
    sync_failed_at to the sidecar; print recovery WARNING.

    This is the load-bearing "never lose work" branch: even on clean
    orchestrator exit, if the sync step that pulls the run branch +
    state back to the host can't succeed, the machine must NOT be
    destroyed — the user's paid LLM work is still on it. The user
    sees a multi-line WARNING and recovers via `leerie finalize`,
    `leerie resume`, or finally `leerie kill` once work is safe."""
    result, sidecar = _decide_teardown_with_rc(
        tmp_path, "0", run_id="my-run", fetch_branch_succeeds=False,
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text() if (tmp_path / "flyctl.log").exists() else ""
    # Machine must NOT be destroyed and must NOT be stopped — the
    # whole point is to leave it running so the user can recover.
    assert "machine destroy" not in invocations
    assert "machine stop" not in invocations
    # Sidecar must record the failure for `leerie list` to surface.
    data = json.loads(sidecar.read_text())
    assert data.get("sync_failed_at") is not None
    assert data.get("sync_fail_reason") == "sync-failed-on-clean-exit"
    assert data.get("fly_machine_id") == "mach-test"
    # Recovery guidance must be printed.
    assert "sync from machine to host FAILED" in result.stderr
    assert "leerie finalize my-run" in result.stderr
    assert "leerie resume my-run" in result.stderr
    assert "leerie kill my-run" in result.stderr


def test_decide_teardown_rc130_detaches(tmp_path: Path):
    """rc=130 (SIGINT) → DETACH: leave machine alone, print reattach hints.

    With the detached orchestrator (DESIGN §6), SIGINT on the launcher means
    the user stopped watching the local tail — not that they want to destroy
    the run. The orchestrator on the machine is still running. The trap
    must neither destroy nor stop the machine, and must print the hints
    that point to --resume / --stop / --kill."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "130", run_id="my-run-abc")
    assert result.returncode == 0, result.stderr
    # No flyctl invocations at all — the stub never gets called.
    log = tmp_path / "flyctl.log"
    assert not log.exists() or log.read_text() == "", \
        f"expected no flyctl calls for rc=130, got: {log.read_text() if log.exists() else ''!r}"
    # Sidecar must be unchanged (no paused_at, no killed_at).
    data = json.loads(sidecar.read_text())
    assert data.get("paused_at") is None
    assert data.get("killed_at") is None
    # Detach hints must appear in stderr.
    assert "detached from run my-run-abc" in result.stderr
    assert "leerie resume my-run-abc" in result.stderr
    assert "leerie stop my-run-abc" in result.stderr
    assert "leerie kill my-run-abc" in result.stderr


def test_decide_teardown_rc143_detaches(tmp_path: Path):
    """rc=143 (SIGTERM) → same detach behavior as SIGINT."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "143", run_id="my-run-xyz")
    assert result.returncode == 0, result.stderr
    log = tmp_path / "flyctl.log"
    assert not log.exists() or log.read_text() == "", \
        f"expected no flyctl calls for rc=143"
    data = json.loads(sidecar.read_text())
    assert data.get("paused_at") is None
    assert data.get("killed_at") is None
    assert "detached from run my-run-xyz" in result.stderr


def test_decide_teardown_rc1_pauses(tmp_path: Path):
    """rc=1 (worker error) → stop (pause), write paused_at sidecar."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "1")
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine stop mach-test" in invocations
    assert "machine destroy mach-test" not in invocations
    data = json.loads(sidecar.read_text())
    assert data["paused_at"] is not None
    assert data["fly_machine_id"] == "mach-test"
    assert data["pause_reason"] == "worker-error"
    assert "PAUSED: machine mach-test" in result.stderr


def test_decide_teardown_rc2_pauses(tmp_path: Path):
    """Any unknown non-zero rc → pause (the default safety mode)."""
    result, sidecar = _decide_teardown_with_rc(tmp_path, "2")
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine stop mach-test" in invocations


def test_decide_teardown_prints_resume_command(tmp_path: Path):
    """The pause notification includes the resume command verbatim."""
    result, _ = _decide_teardown_with_rc(tmp_path, "1", run_id="my-run-abc")
    assert "leerie resume my-run-abc" in result.stderr


def test_decide_teardown_pause_reason_overridable(tmp_path: Path):
    """LEERIE_PAUSE_REASON env var overrides the default 'worker-error' tag."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-001"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({"run_id": "test-001"}))
    result = _run_bash(
        f"source {PROVISION_SH}; LEERIE_MACHINE_ID=mach-test; decide_teardown",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-001",
            "LEERIE_REMOTE_EXIT_RC": "1",
            "LEERIE_PAUSE_REASON": "finalize-failed",
        },
    )
    assert result.returncode == 0
    data = json.loads(sidecar.read_text())
    assert data["pause_reason"] == "finalize-failed"


def test_decide_teardown_pause_notify_cmd_invoked(tmp_path: Path):
    """LEERIE_PAUSE_NOTIFY_CMD is eval'd on pause for outbound notifications."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-001"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({"run_id": "test-001"}))
    notify_marker = tmp_path / "notify-fired"
    result = _run_bash(
        f"source {PROVISION_SH}; LEERIE_MACHINE_ID=mach-test; decide_teardown",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-001",
            "LEERIE_REMOTE_EXIT_RC": "1",
            "LEERIE_PAUSE_NOTIFY_CMD": f"touch {notify_marker}",
        },
    )
    assert result.returncode == 0
    assert notify_marker.exists(), "LEERIE_PAUSE_NOTIFY_CMD was not eval'd on pause"


# --- resume-machine.sh ----------------------------------------------------

def test_resume_machine_requires_machine_id(tmp_path: Path):
    """resume_machine errors when no machine id is passed."""
    result = _run_bash(
        f"source {RESUME_SH}; resume_machine",
    )
    assert result.returncode != 0
    assert "machine id required" in result.stderr


def test_resume_machine_calls_start_and_clears_paused_at(tmp_path: Path):
    """resume_machine starts the machine, waits for started, clears paused_at."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-001"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-001",
        "paused_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "mach-resumed",
        "pause_reason": "worker-error",
    }))
    # Source provision.sh first so wait_for_started is available.
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-resumed",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-001",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine start mach-resumed" in invocations
    data = json.loads(sidecar.read_text())
    assert data["paused_at"] is None
    assert data["pause_reason"] is None
    # fly_machine_id is preserved — useful for post-resume inspection.
    assert data["fly_machine_id"] == "mach-resumed"


def test_resume_machine_refuses_destroyed_machine(tmp_path: Path):
    """resume_machine errors when the machine has been destroyed."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "  'machine start') exit 1 ;;\n"
        "  'machine status') printf 'Machine ID: mach-001\\nState: destroyed\\n'; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-gone",
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "destroyed" in result.stderr or "no longer recoverable" in result.stderr


def test_resume_machine_updates_image_on_version_drift(tmp_path: Path):
    """When image_tag in run.json differs from FLY_IMAGE_TAG, resume_machine
    calls flyctl machine update --image before starting."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "  'machine update') exit 0 ;;\n"
        "  'machine start') exit 0 ;;\n"
        "  'machine status') printf 'Machine ID: mach-001\\nState: started\\n'; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-002"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-002",
        "paused_at": "2026-06-01T10:00:00+00:00",
        "fly_machine_id": "mach-upgrade",
        "pause_reason": "worker-error",
        "image_tag": "registry.fly.io/leerie:0.6.6",
    }))
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-upgrade",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-002",
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:0.6.7",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = log.read_text()
    assert "machine update mach-upgrade --image registry.fly.io/leerie:0.6.7" in invocations
    assert "machine start mach-upgrade" in invocations
    lines = invocations.splitlines()
    update_idx = next(i for i, l in enumerate(lines) if "machine update" in l)
    start_idx = next(i for i, l in enumerate(lines) if "machine start" in l)
    assert update_idx < start_idx, "machine update must precede machine start"
    data = json.loads(sidecar.read_text())
    assert data["image_tag"] == "registry.fly.io/leerie:0.6.7"
    assert data["paused_at"] is None


def test_resume_machine_skips_image_update_when_same_version(tmp_path: Path):
    """When image_tag matches FLY_IMAGE_TAG, no machine update is issued."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-003"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-003",
        "paused_at": "2026-06-01T10:00:00+00:00",
        "fly_machine_id": "mach-same",
        "pause_reason": "worker-error",
        "image_tag": "registry.fly.io/leerie:0.6.7",
    }))
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-same",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-003",
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:0.6.7",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine update" not in invocations
    assert "machine start mach-same" in invocations


def test_resume_machine_updates_image_when_no_stored_tag(tmp_path: Path):
    """Legacy machines with no stored image_tag get updated to the current
    image on resume (c3670af: empty stored tag != current tag → update)."""
    _make_flyctl_stub(tmp_path, behavior="happy")
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-004"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-004",
        "paused_at": "2026-06-01T10:00:00+00:00",
        "fly_machine_id": "mach-legacy",
        "pause_reason": "worker-error",
    }))
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-legacy",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-004",
            "LEERIE_FLY_APP": "leerie",
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:0.6.7",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine update mach-legacy --image registry.fly.io/leerie:0.6.7" in invocations


def test_resume_machine_continues_on_image_update_failure(tmp_path: Path):
    """Fail-open: if flyctl machine update fails, resume_machine logs a
    warning and proceeds with the old image — the resume is not blocked."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "  'machine update') exit 1 ;;\n"
        "  'machine start') exit 0 ;;\n"
        "  'machine status') printf 'Machine ID: mach-001\\nState: started\\n'; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    user_repo = tmp_path / "user-repo"
    run_dir = user_repo / ".leerie" / "runs" / "test-005"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-005",
        "paused_at": "2026-06-01T10:00:00+00:00",
        "fly_machine_id": "mach-failopen",
        "pause_reason": "worker-error",
        "image_tag": "registry.fly.io/leerie:0.6.6",
    }))
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-failopen",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_RUN_ID": "test-005",
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:0.6.7",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "image update failed" in result.stderr
    invocations = log.read_text()
    assert "machine start mach-failopen" in invocations
    data = json.loads(sidecar.read_text())
    assert data["image_tag"] == "registry.fly.io/leerie:0.6.6", \
        "sidecar should retain old tag when update fails"


def test_resume_machine_image_update_via_state_host_dir(tmp_path: Path):
    """Image-update sidecar resolution prefers LEERIE_STATE_HOST_DIR over
    USER_REPO, matching provision.sh's pattern."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        "  'machine update') exit 0 ;;\n"
        "  'machine start') exit 0 ;;\n"
        "  'machine status') printf 'Machine ID: mach-001\\nState: started\\n'; exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    state_dir = tmp_path / "state-dir"
    run_dir = state_dir / "runs" / "test-006"
    run_dir.mkdir(parents=True)
    sidecar = run_dir / "run.json"
    sidecar.write_text(json.dumps({
        "run_id": "test-006",
        "paused_at": "2026-06-01T10:00:00+00:00",
        "fly_machine_id": "mach-statedir",
        "pause_reason": "worker-error",
        "image_tag": "registry.fly.io/leerie:0.6.6",
    }))
    result = _run_bash(
        f"source {PROVISION_SH}; source {RESUME_SH}; resume_machine mach-statedir",
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "LEERIE_RUN_ID": "test-006",
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:0.6.7",
        },
    )
    assert result.returncode == 0, result.stderr
    invocations = log.read_text()
    assert "machine update mach-statedir --image registry.fly.io/leerie:0.6.7" in invocations
    data = json.loads(sidecar.read_text())
    assert data["image_tag"] == "registry.fly.io/leerie:0.6.7"
    assert data["paused_at"] is None


# --- coupling: launcher pause-print includes the resume command ----------

def test_launcher_resume_command_format_matches_decide_teardown():
    """Coupling: the resume command printed by decide_teardown must use
    positional run-id, matching the launcher's positional-arg parsing.
    """
    provision = PROVISION_SH.read_text()
    assert "leerie resume ${LEERIE_RUN_ID:-<run-id>}" in provision, (
        "decide_teardown's resume hint string drifted"
    )
