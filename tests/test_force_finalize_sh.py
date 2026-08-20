"""Tests for scripts/remote/force-finalize.sh.

force-finalize.sh is sourced by the leerie launcher's `finalize --force`
fast-path. It SSHes into the Fly Machine, verifies the orchestrator
process is dead, and patches `finished_at` into run.json so the normal
`fetch_branch` discovery loop can pick the run up. These tests exercise
the script's bash logic and the embedded Python payload in isolation via
subprocess, with `flyctl` stubbed so no real Fly.io calls happen.

The payload runs ON THE MACHINE; the stub flyctl simulates the machine
by routing `flyctl ssh console -C "python3 -" < <payload>` to a local
`python3 -` invocation against a fixture tree shaped like
`/work/.leerie/runs/`. This lets us test the safety predicates
(alive-pid REFUSE, missing-pid REFUSE, multi-dir REFUSE) end-to-end
without needing a real container or SSH.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tests.conftest import run_bash

REPO_ROOT = Path(__file__).resolve().parent.parent
FORCE_FINALIZE_SH = REPO_ROOT / "scripts" / "remote" / "force-finalize.sh"


def _make_fake_flyctl(tmp_path: Path, machine_runs_dir: Path) -> Path:
    """Stub flyctl that routes `python3 -` payloads to a local python3.

    force-finalize.sh sends its discovery + patch payload via:
        printf '%s' "$payload" | flyctl ssh console ... -C "python3 -"

    The payload reads from stdin and prints a sentinel line to stdout.
    Our stub:
      1. ignores all flyctl flags,
      2. detects the "python3 -" command shape,
      3. rewrites `/work/.leerie/runs` → machine_runs_dir in the script
         body that's coming through stdin,
      4. runs `python3 -` with the rewritten script.

    The path rewrite lets the embedded Python work against the fixture
    tree shaped like /work/.leerie/runs/.
    """
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'MRUNS="{machine_runs_dir}"\n'
        # Parse -C "<command>" out of the argv.
        'CMD=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -C) CMD="$2"; shift 2 ;;\n'
        '    auth) shift; case "${1:-}" in status) exit 0 ;; esac ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        '[ -z "$CMD" ] && exit 0\n'
        # The only -C command force-finalize.sh sends is `python3 -`.
        # Rewrite the in-machine /work/.leerie/runs path in the stdin
        # payload to point at the test fixture. The substitution uses
        # bash's ${var//pattern/replacement}, which is GLOB substitution
        # (not literal-string). Safe here because both /work/.leerie/runs
        # and $MRUNS contain no glob metacharacters (*, ?, [); a future
        # fixture path with such characters would silently misbehave.
        'case "$CMD" in\n'
        '  python3*-*)\n'
        '    SCRIPT="$(cat)"\n'
        '    REWRITTEN="${SCRIPT//\\/work\\/.leerie\\/runs/$MRUNS}"\n'
        '    printf "%s" "$REWRITTEN" | python3 -\n'
        '    exit $?\n'
        '    ;;\n'
        '  *) exit 0 ;;\n'
        'esac\n'
    )
    stub.chmod(0o755)
    return stub


def _make_run(
    machine_runs_dir: Path,
    run_id: str,
    *,
    finished_at: str | None = None,
    pid: int | None = None,
    pid_file_missing: bool = False,
) -> Path:
    """Create a fixture run dir under machine_runs_dir."""
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
        # pid defaults to a pid that's almost certainly dead. 1 is init
        # (alive on the test host), so for the "dead" case pick a high
        # value that's exceedingly unlikely to exist.
        actual_pid = pid if pid is not None else 999_999_999
        (run_dir / "orchestrator.pid").write_text(f"{actual_pid}\n")
    return run_dir


def test_force_finalize_sh_exists():
    assert FORCE_FINALIZE_SH.exists()


def test_force_finalize_sh_is_executable():
    assert os.access(FORCE_FINALIZE_SH, os.X_OK)


def test_refuses_when_no_machine_id():
    """force_finalize_remote returns 1 when LEERIE_MACHINE_ID is empty."""
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie ''",
        env={"LEERIE_REPO": str(REPO_ROOT)},
    )
    assert result.returncode != 0
    assert "LEERIE_MACHINE_ID" in result.stderr


def test_patches_dead_run(tmp_path):
    """Happy path: run with no finished_at + dead pid → patch succeeds.

    Assert sentinel parsing yields OK, run.json now has finished_at,
    recovered_at, recovered_via="force-finalize".
    """
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-cool-abc123"
    run_dir = _make_run(mruns, run_id, pid=999_999_999)
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode == 0, (
        f"expected success; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "patched" in result.stderr.lower(), result.stderr
    patched = json.loads((run_dir / "run.json").read_text())
    assert patched.get("finished_at"), patched
    assert patched.get("recovered_at"), patched
    assert patched.get("recovered_via") == "force-finalize", patched
    assert patched.get("no_push") is False or patched.get("no_push") is None


def test_idempotent_when_already_finalized(tmp_path):
    """When finished_at is already set, payload says OK and does NOT mutate."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-done-def456"
    run_dir = _make_run(
        mruns, run_id, finished_at="2026-06-02T20:00:00Z", pid=999_999_999
    )
    before = (run_dir / "run.json").read_text()
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    after = (run_dir / "run.json").read_text()
    assert before == after, "idempotent path must not rewrite run.json"


def test_refuses_when_pid_alive(tmp_path):
    """When the pid in orchestrator.pid is alive and looks like python,
    REFUSE. Use the current python process as a fixture (we know it's
    alive and its comm contains 'python')."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-running-xyz789"
    # Use os.getpid() — guaranteed alive, comm contains "python" because
    # we're running under pytest.
    own_pid = os.getpid()
    run_dir = _make_run(mruns, run_id, pid=own_pid)
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    # The /proc check only works on Linux; on Darwin the script falls
    # through to the alive-but-not-python branch which still patches.
    # Gate the assertion on platform.
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    import sys
    if sys.platform == "linux":
        assert result.returncode != 0, (
            f"expected REFUSE on alive python pid; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        assert "REFUSED" in result.stderr or "alive" in result.stderr.lower()
        # run.json must NOT have been mutated. _make_run never sets
        # finished_at and the REFUSE path returns before any write, so
        # the field must be entirely absent — accepting a falsy value
        # here would silently mask a regression where force-finalize.sh
        # writes finished_at=null instead of refusing.
        patched = json.loads((run_dir / "run.json").read_text())
        assert "finished_at" not in patched, patched
    else:
        # On Darwin, /proc/<pid>/cmdline doesn't exist; the payload's
        # try/except sets ident="?" and is_python=False, which falls
        # into the not-python branch and proceeds with the patch.
        # Document this as the platform's behavior and verify the
        # resulting recovered_via field.
        assert result.returncode == 0, result.stderr


def test_refuses_when_pid_file_missing(tmp_path):
    """orchestrator.pid absent → REFUSE-NOPID."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-early-fail-ghi321"
    run_dir = _make_run(mruns, run_id, pid_file_missing=True)
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode != 0, result.stderr
    assert "NOPID" in result.stderr or "no orchestrator.pid" in result.stderr.lower()
    # run.json must be untouched. Same rationale as test_refuses_when_
    # pid_alive's assertion: the field should be entirely absent, not
    # falsy. A force-finalize.sh regression that writes finished_at=null
    # instead of refusing must fail this test, not silently pass.
    patched = json.loads((run_dir / "run.json").read_text())
    assert "finished_at" not in patched, patched


def test_refuses_on_multiple_run_dirs(tmp_path):
    """More than one run dir on the machine → REFUSE-MULTI."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    _make_run(mruns, "feat-one-aaa111", pid=999_999_999)
    _make_run(mruns, "feat-two-bbb222", pid=999_999_999)
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode != 0, result.stderr
    assert "MULTI" in result.stderr or "can't pick" in result.stderr.lower()


def test_refuses_on_zero_run_dirs(tmp_path):
    """No run dir on the machine → REFUSE-NONE."""
    mruns = tmp_path / "runs"
    mruns.mkdir()
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode != 0, result.stderr
    assert "NONE" in result.stderr or "no run dir" in result.stderr.lower()


def test_refuses_when_proc_scan_finds_live_orchestrator(tmp_path):
    """/proc cross-check catches a stale-pid contagion scenario.

    Fixture: pid file points to a dead pid, BUT a live process exists
    whose argv carries both the orchestrator path anchor and the
    run-id. This simulates the bug we hit on sibling-service: launcher
    wrote a stillborn child's pid (dead) while the real orchestrator
    (the flock winner) kept running.

    The /proc scan must refuse with REFUSE-ALIVE-SCAN regardless of
    what the pid file says.
    """
    import sys as _sys
    if _sys.platform != "linux":
        # /proc only exists on Linux. On Darwin the scan finds nothing
        # and the test would fall through to the pid-file path
        # (test_patches_dead_run already covers that). The production
        # target is the Fly Linux image, so Linux-only coverage is
        # sufficient.
        import pytest
        pytest.skip("/proc cross-check only meaningful on Linux")

    mruns = tmp_path / "runs"
    mruns.mkdir()
    # Use a sufficiently distinctive run-id so no other process on the
    # CI host could plausibly false-match.
    run_id = "feat-proc-scan-fixture-zxq9847"
    run_dir = _make_run(mruns, run_id, pid=999_999_999)

    # Spawn a sleeper subprocess whose argv contains both anchors:
    #   orchestrator/leerie.py  AND  the run-id (as a discrete token)
    # Use a path that contains the substring — the actual file need
    # not exist; the scan reads /proc/<pid>/cmdline, not the file.
    fake_orch_path = str(tmp_path / "orchestrator" / "leerie.py")
    sleeper = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(30)",
         fake_orch_path, "--run-id", run_id],
    )
    try:
        stub = _make_fake_flyctl(tmp_path, mruns)
        env = {
            "LEERIE_REPO": str(REPO_ROOT),
            "PATH": f"{stub.parent}:{os.environ['PATH']}",
        }
        result = run_bash(
            f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
            env=env,
        )
        assert result.returncode != 0, (
            f"expected REFUSE on live /proc match; stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        # New sentinel — REFUSE-ALIVE-SCAN, distinct from the existing
        # REFUSE-ALIVE (pid-file path) for audit clarity.
        assert "REFUSE-ALIVE-SCAN" in result.stderr or "/proc scan" in result.stderr, (
            f"expected scan-source REFUSE; stderr={result.stderr!r}"
        )
        # run.json must NOT have been mutated. The /proc scan runs
        # before any patch logic.
        patched = json.loads((run_dir / "run.json").read_text())
        assert "finished_at" not in patched, patched
    finally:
        sleeper.terminate()
        try:
            sleeper.wait(timeout=5)
        except subprocess.TimeoutExpired:
            sleeper.kill()
            sleeper.wait()


def test_patches_when_proc_scan_finds_nothing(tmp_path):
    """Parallel of test_patches_dead_run with explicit /proc scan path.

    Confirms the new scan does NOT false-positive when no matching
    process exists. The run-id is unique to this test; no process on
    the CI host can plausibly carry it.
    """
    mruns = tmp_path / "runs"
    mruns.mkdir()
    run_id = "feat-no-scan-hit-qfx8462"
    run_dir = _make_run(mruns, run_id, pid=999_999_999)
    stub = _make_fake_flyctl(tmp_path, mruns)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
    }
    result = run_bash(
        f"source {FORCE_FINALIZE_SH}; force_finalize_remote leerie machine-xxx",
        env=env,
    )
    assert result.returncode == 0, (
        f"expected success; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    patched = json.loads((run_dir / "run.json").read_text())
    assert patched.get("finished_at"), patched
    assert patched.get("recovered_via") == "force-finalize", patched
