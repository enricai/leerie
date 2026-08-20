"""Tests for the FORCE_STOP mode in scripts/remote/force-finalize.sh.

When FORCE_STOP=1 is set, the force-finalize payload SIGTERMs the
orchestrator process instead of refusing when it's alive, waits for it
to die, then falls through to the normal finished_at patch. These tests
exercise that behavior via the same stubbed-flyctl pattern used by
test_force_finalize_sh.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.conftest import run_bash
from tests.fly_stub import make_fake_flyctl

REPO_ROOT = Path(__file__).resolve().parent.parent
FORCE_FINALIZE_SH = REPO_ROOT / "scripts" / "remote" / "force-finalize.sh"


def _flyctl_stub(tmp_path: Path, machine_runs_dir: Path) -> Path:
    """Stub flyctl that routes payloads to local python3/bash.

    For FORCE_STOP=1 mode, the SSH command is:
        bash -lc 'FORCE_STOP=1 exec python3 -'
    The stub detects both forms and routes appropriately.
    """
    extra_preamble = f'MRUNS="{machine_runs_dir}"\n'
    case_body = (
        '[ -z "$CMD" ] && exit 0\n'
        'case "$CMD" in\n'
        # FORCE_STOP mode: bash -lc 'FORCE_STOP=1 exec python3 -'
        "  bash*FORCE_STOP*python3*)\n"
        '    SCRIPT="$(cat)"\n'
        '    REWRITTEN="${SCRIPT//\\/work\\/.leerie\\/runs/$MRUNS}"\n'
        '    export FORCE_STOP=1\n'
        '    printf "%s" "$REWRITTEN" | python3 -\n'
        '    exit $?\n'
        '    ;;\n'
        # Normal mode: python3 -
        '  python3*-*)\n'
        '    SCRIPT="$(cat)"\n'
        '    REWRITTEN="${SCRIPT//\\/work\\/.leerie\\/runs/$MRUNS}"\n'
        '    printf "%s" "$REWRITTEN" | python3 -\n'
        '    exit $?\n'
        '    ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n'
    )
    return make_fake_flyctl(tmp_path, case_body, extra_preamble=extra_preamble)


def _make_run(
    machine_runs_dir: Path,
    run_id: str,
    *,
    pid: int | None = None,
    pid_file_missing: bool = False,
    finished_at: str | None = None,
) -> Path:
    run_dir = machine_runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sidecar = {
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
        "working_branch": "main",
        "started_at": "2026-06-02T00:00:00+00:00",
    }
    if finished_at:
        sidecar["finished_at"] = finished_at
    (run_dir / "run.json").write_text(json.dumps(sidecar, indent=2))
    if not pid_file_missing:
        actual_pid = pid if pid is not None else 999_999_999
        (run_dir / "orchestrator.pid").write_text(f"{actual_pid}\n")
    return run_dir


def test_force_stop_patches_dead_run(tmp_path):
    """FORCE_STOP=1 with a dead orchestrator: should patch normally
    (same as without FORCE_STOP, since orchestrator is already dead)."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-dead-abc123"
    run_dir = _make_run(mruns, run_id, pid=999_999_999)
    stub = _flyctl_stub(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "FORCE_STOP": "1",
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; FORCE_STOP=1 force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode == 0, f"stderr={result.stderr}"
    patched = json.loads((run_dir / "run.json").read_text())
    assert patched.get("finished_at"), patched
    assert patched.get("recovered_via") == "force-finalize"


@pytest.mark.skipif(sys.platform != "linux",
                    reason="FORCE_STOP pid kill test only meaningful on Linux")
def test_force_stop_kills_alive_pid(tmp_path):
    """FORCE_STOP=1 with a live python process: should SIGTERM it,
    wait for death, then patch. Uses a sleep subprocess as the target."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-alive-xyz789"

    # Spawn a sleep process that we can safely kill. We write a tiny
    # Python script that sleeps, so /proc/<pid>/cmdline contains "python".
    sleeper = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(300)"],
    )
    try:
        run_dir = _make_run(mruns, run_id, pid=sleeper.pid)
        stub = _flyctl_stub(tmp_path, mruns)
        env = {
            "LEERIE_REPO": str(REPO_ROOT),
            "FORCE_STOP": "1",
            "PATH": f"{stub.parent}:{os.environ['PATH']}",
        }
        result = run_bash(
            f"source {FORCE_FINALIZE_SH}; FORCE_STOP=1 force_finalize_remote leerie machine-xxx",
            env=env,
        )
        assert result.returncode == 0, (
            f"expected success after killing pid; "
            f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "stopped" in result.stderr.lower(), (
            f"expected 'stopped' in stderr; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        patched = json.loads((run_dir / "run.json").read_text())
        assert patched.get("finished_at"), patched
        assert patched.get("recovered_via") == "force-finalize"

        # The sleeper should be dead now.
        sleeper.wait(timeout=5)
    finally:
        sleeper.kill()
        sleeper.wait()


def test_force_stop_idempotent_already_finalized(tmp_path):
    """FORCE_STOP=1 on an already-finalized run: should return OK
    without touching run.json."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-done-abc"
    run_dir = _make_run(mruns, run_id, finished_at="2026-06-02T20:00:00Z")
    before = (run_dir / "run.json").read_text()
    stub = _flyctl_stub(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "FORCE_STOP": "1",
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; FORCE_STOP=1 force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    after = (run_dir / "run.json").read_text()
    assert before == after


def test_without_force_stop_still_refuses_alive(tmp_path):
    """Without FORCE_STOP, an alive python pid should still be REFUSED
    (regression guard: the new code path must not change non-force behavior)."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-running-reg"
    own_pid = os.getpid()
    _make_run(mruns, run_id, pid=own_pid)
    stub = _flyctl_stub(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    if sys.platform == "linux":
        assert result.returncode != 0, (
            f"expected REFUSE; stderr={result.stderr}"
        )
        assert "REFUSED" in result.stderr or "alive" in result.stderr.lower()
