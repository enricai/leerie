"""Part D D2: `leerie --stop`/`--kill`/`--finalize` must accept the
bootstrap-id (`_bootstrap-<6hex>`) of an in-flight detached fly run.

During the bootstrap window (~1 min from launch to end-of-classify),
the host has only `.leerie/runs/_bootstrap-<id>/fly-machine.json` — the
orchestrator has not yet written `run.json` (that lives on the Fly
Machine). The verbs need to fall back to `fly-machine.json` for the
`fly_machine_id` lookup, matching scripts/remote/attach.sh's
resolution order.

These tests stub `flyctl` (records argv to flyctl.log) so we exercise
the launcher's resolution + sidecar-update logic without touching a
real Fly Machine.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"


def _stub_flyctl(tmp_path: Path) -> None:
    """Write a stub `flyctl` that records its argv and exits 0.
    Also stubs `python3` is not needed — it's already on PATH."""
    p = tmp_path / "flyctl"
    p.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {tmp_path}/flyctl.log\n"
        "exit 0\n"
    )
    p.chmod(0o755)


def _state_dir(tmp_path: Path) -> Path:
    """Return the canonical state dir for these tests: a fixed subdir of
    tmp_path that LEERIE_STATE_DIR is set to so all leerie verbs agree
    on where to look for run dirs."""
    return tmp_path / "leerie-state"


def _bootstrap_run(tmp_path: Path, run_id: str, machine_id: str) -> Path:
    """Set up `<state_dir>/runs/<run_id>/fly-machine.json` with no run.json.
    This is the in-flight bootstrap state on the host."""
    run_dir = _state_dir(tmp_path) / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "fly-machine.json").write_text(json.dumps({
        "fly_machine_id": machine_id,
        "fly_app": "leerie",
    }))
    return run_dir


def _run_leerie(tmp_path: Path, *args: str,
              stdin: str = "") -> subprocess.CompletedProcess:
    """Run leerie with stubbed flyctl on PATH. cwd is the fake user-repo
    so leerie's USER_REPO=$(pwd -P) resolves there (the launcher hard-
    overrides USER_REPO from $PWD at line 39, ignoring the env var).
    LEERIE_STATE_DIR is set to the test's canonical state dir so sidecar
    lookups find the fixtures created by _bootstrap_run."""
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir(exist_ok=True)
    return subprocess.run(
        [str(LEERIE), *args],
        cwd=str(user_repo),
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "LEERIE_STATE_DIR": str(_state_dir(tmp_path)),
            "LEERIE_NO_RUNTIME_INSTALL": "1",  # don't trigger install path
        },
        input=stdin,
        capture_output=True, text=True, check=False,
    )


# --- D2 fallback: --stop ---------------------------------------------------

def test_stop_accepts_bootstrap_id_with_only_fly_machine_json(tmp_path: Path):
    """`leerie --stop _bootstrap-aaaaaa` must work when the host has
    only fly-machine.json (no run.json yet). This is exactly the
    state the detach banner points the user at."""
    _stub_flyctl(tmp_path)
    run_dir = _bootstrap_run(tmp_path, "_bootstrap-aaaaaa", "mach-boot1")
    result = _run_leerie(tmp_path, "--stop", "_bootstrap-aaaaaa")
    assert result.returncode == 0, \
        f"--stop failed for bootstrap id; stderr:\n{result.stderr}"
    # flyctl must have been invoked with `machine stop mach-boot1`.
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine stop mach-boot1" in invocations, \
        f"expected machine stop call; got:\n{invocations}"
    # run.json was bootstrapped with paused_at set.
    sidecar = run_dir / "run.json"
    assert sidecar.exists(), "expected run.json to be created"
    data = json.loads(sidecar.read_text())
    assert data.get("paused_at") is not None
    assert data.get("pause_reason") == "user-requested"
    assert data.get("fly_machine_id") == "mach-boot1"


# --- D2 fallback: --kill ---------------------------------------------------

def test_kill_accepts_bootstrap_id_with_only_fly_machine_json(tmp_path: Path):
    """`leerie --kill _bootstrap-bbbbbb --force` works against a bootstrap dir."""
    _stub_flyctl(tmp_path)
    run_dir = _bootstrap_run(tmp_path, "_bootstrap-bbbbbb", "mach-boot2")
    result = _run_leerie(tmp_path, "--kill", "_bootstrap-bbbbbb", "--force")
    assert result.returncode == 0, \
        f"--kill failed for bootstrap id; stderr:\n{result.stderr}"
    invocations = (tmp_path / "flyctl.log").read_text()
    assert "machine destroy mach-boot2" in invocations, \
        f"expected machine destroy call; got:\n{invocations}"
    # run.json was bootstrapped with killed_at set.
    sidecar = run_dir / "run.json"
    assert sidecar.exists(), "expected run.json to be created"
    data = json.loads(sidecar.read_text())
    assert data.get("killed_at") is not None
    assert data.get("fly_machine_id") == "mach-boot2"


# --- D3: --kill confirmation label is context-aware -----------------------

def test_kill_orphan_prompt_says_machine_id_not_run_id(tmp_path: Path):
    """When the user passes --machine-id without a run-id, the
    confirmation prompt label must say "machine-id" (not "run-id")
    because the user has no run-id to type."""
    _stub_flyctl(tmp_path)
    # No run dir at all — orphan mode.
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    # Type a wrong confirmation token to trigger the prompt path and
    # capture the prompt text from stderr.
    result = _run_leerie(tmp_path, "--kill", "--machine-id", "mach-orphan-xyz",
                       stdin="wrong-token\n")
    # Confirmation mismatch — exit 1 — but the prompt text is what we
    # care about, and it's printed before read.
    assert "Type the machine-id to confirm" in result.stderr, \
        f"expected 'Type the machine-id to confirm' in stderr; got:\n{result.stderr}"
    assert "Type the run-id to confirm" not in result.stderr


def test_kill_run_id_prompt_says_run_id(tmp_path: Path):
    """The opposite of the above: when a run-id is given, the prompt
    label says "run-id"."""
    _stub_flyctl(tmp_path)
    _bootstrap_run(tmp_path, "_bootstrap-ccccc1", "mach-ccccc1")
    result = _run_leerie(tmp_path, "--kill", "_bootstrap-ccccc1",
                       stdin="wrong-token\n")
    assert "Type the run-id to confirm" in result.stderr, \
        f"expected 'Type the run-id to confirm' in stderr; got:\n{result.stderr}"
    assert "Type the machine-id to confirm" not in result.stderr
