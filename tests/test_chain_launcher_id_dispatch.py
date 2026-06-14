"""Tests for the launcher's ID-dispatched chain verbs (v5 Shape A).

Under Shape A, chain-scoped verbs (--status, --kill, --stop, --resume,
--attach, --finalize) detect UUID-pattern positional ids and dispatch
by iterating ``$LEERIE_STATE_HOST_DIR/runs/*/run.json`` for runs with a
matching ``chain_id`` field. There is no Fly coordinator; chains exist
only as the set of single runs sharing a chain_id tag.

These tests stub a run.json fixture per chain run and verify that the
launcher correctly discovers, filters, and acts on them. ``$0 --kill``
/ ``$0 --resume`` / etc. are replaced with a stub recorder so the
tests don't actually shell out to Fly.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

CHAIN_ID = "abcdef01-2345-4789-89ab-0123456789ab"
RUN_ID_1 = "abc123def45601"
RUN_ID_2 = "abc123def45602"
NON_CHAIN_RUN = "ff0000000000ff"


def _write_run(state_dir: Path, run_id: str, fields: dict) -> None:
    """Materialize $state_dir/runs/<run_id>/run.json with *fields*."""
    rd = state_dir / "runs" / run_id
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(json.dumps(fields))


def _fixture_two_run_chain(tmp_path: Path) -> Path:
    """Two paused chain runs + one unrelated non-chain run."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    _write_run(state_dir, RUN_ID_1, {
        "run_id": RUN_ID_1,
        "branch": f"leerie/runs/{RUN_ID_1}",
        "chain_id": CHAIN_ID,
        "wave_idx": 0,
        "paused_at": "2026-06-14T00:00:00Z",
        "pause_reason": "worker-error",
        "fly_machine_id": RUN_ID_1,
    })
    _write_run(state_dir, RUN_ID_2, {
        "run_id": RUN_ID_2,
        "branch": f"leerie/runs/{RUN_ID_2}",
        "chain_id": CHAIN_ID,
        "wave_idx": 0,
        "pushed_at": "2026-06-14T01:00:00Z",
        "pr_url": "https://github.com/x/y/pull/1",
        "fly_machine_id": RUN_ID_2,
    })
    # Unrelated run (no chain_id).
    _write_run(state_dir, NON_CHAIN_RUN, {
        "run_id": NON_CHAIN_RUN,
        "branch": f"leerie/runs/{NON_CHAIN_RUN}",
        "fly_machine_id": NON_CHAIN_RUN,
    })
    return state_dir


def _stub_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Build a stub binary that records its argv. Exposed to the launcher
    via the ``LEERIE_SELF_CMD`` env override so chain-id dispatch invokes
    the stub instead of the real launcher for per-run recursion.

    Returns ``(stub_path, log_path)``.
    """
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _run(tmp_path: Path, args: list[str], env_extra: dict | None = None,
         use_self_stub: bool = True) -> subprocess.CompletedProcess:
    state_dir = tmp_path / ".leerie" / "myrepo"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
    }
    if env_extra:
        env.update(env_extra)
    stub_log = None
    if use_self_stub:
        stub_path, stub_log = _stub_self_cmd(tmp_path)
        env["LEERIE_SELF_CMD"] = str(stub_path)
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )
    result.stub_log = stub_log.read_text() if stub_log and stub_log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# --list --chains
# ---------------------------------------------------------------------------


def test_list_chains_groups_by_chain_id(tmp_path: Path) -> None:
    """--list --chains discovers chains by iterating run.json + grouping."""
    _fixture_two_run_chain(tmp_path)
    result = _run(tmp_path, ["--list", "--chains"], use_self_stub=False)
    assert result.returncode == 0, result.stderr
    assert CHAIN_ID in result.stdout
    assert "chain_id" in result.stdout  # header row
    # Per-chain summary should reflect 2 runs (1 pushed, 1 paused).
    assert "1/2" in result.stdout  # pushed count of 1 out of 2 total
    assert "paused" in result.stdout  # 1 paused → chain status: paused


def test_list_chains_empty(tmp_path: Path) -> None:
    """No chain_id-tagged runs → friendly empty message."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    # Non-chain run only.
    _write_run(state_dir, NON_CHAIN_RUN, {"run_id": NON_CHAIN_RUN})
    result = _run(tmp_path, ["--list", "--chains"], use_self_stub=False)
    assert result.returncode == 0
    assert "no chains" in result.stdout.lower()


def test_list_chains_via_deprecated_alias(tmp_path: Path) -> None:
    """--list-chains alias shims to --list --chains."""
    _fixture_two_run_chain(tmp_path)
    result = _run(tmp_path, ["--list-chains"], use_self_stub=False)
    assert result.returncode == 0
    assert CHAIN_ID in result.stdout


# ---------------------------------------------------------------------------
# --status <chain-id>
# ---------------------------------------------------------------------------


def test_status_uuid_enumerates_chain_runs(tmp_path: Path) -> None:
    """--status <chain-id> lists every run with matching chain_id."""
    _fixture_two_run_chain(tmp_path)
    result = _run(tmp_path, ["--status", CHAIN_ID], use_self_stub=False)
    assert result.returncode == 0, result.stderr
    assert RUN_ID_1 in result.stdout
    assert RUN_ID_2 in result.stdout
    # The unrelated non-chain run must NOT appear.
    assert NON_CHAIN_RUN not in result.stdout


def test_status_uuid_no_matches_errors(tmp_path: Path) -> None:
    """UUID with no matching runs → non-zero exit with a clear message."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    result = _run(tmp_path, ["--status", CHAIN_ID], use_self_stub=False)
    assert result.returncode != 0
    assert "no runs found" in (result.stdout + result.stderr).lower()


def test_status_non_uuid_falls_through_with_hint(tmp_path: Path) -> None:
    """A non-UUID id → routes the user to the single-run inspection verb."""
    result = _run(tmp_path, ["--status", "not-a-uuid"], use_self_stub=False)
    assert result.returncode != 0
    assert "looks like a run-id" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# --kill <chain-id>
# ---------------------------------------------------------------------------


def test_kill_uuid_dispatches_per_chain_run(tmp_path: Path) -> None:
    """--kill <chain-id> invokes single-run --kill for each chain run."""
    _fixture_two_run_chain(tmp_path)
    result = _run(tmp_path, ["--kill", CHAIN_ID])
    assert result.returncode == 0, result.stderr
    # The stub records every recursive ``$0 --kill <run-id>`` call.
    stub = result.stub_log
    # RUN_ID_2 is already pushed (no fly_machine_id matters? it's still
    # present); but our discovery filters out runs with no
    # fly_machine_id OR with killed_at set. Both fixture runs have
    # fly_machine_id and neither has killed_at, so both should be
    # dispatched.
    assert f"--kill {RUN_ID_1}" in stub
    assert f"--kill {RUN_ID_2}" in stub
    # Non-chain run never appears.
    assert NON_CHAIN_RUN not in stub


def test_kill_uuid_skips_already_killed_runs(tmp_path: Path) -> None:
    """--kill <chain-id> does not re-kill runs whose killed_at is set."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    _write_run(state_dir, RUN_ID_1, {
        "run_id": RUN_ID_1, "chain_id": CHAIN_ID, "wave_idx": 0,
        "fly_machine_id": RUN_ID_1,
        "killed_at": "2026-06-14T03:00:00Z",
    })
    _write_run(state_dir, RUN_ID_2, {
        "run_id": RUN_ID_2, "chain_id": CHAIN_ID, "wave_idx": 0,
        "fly_machine_id": RUN_ID_2,
    })
    result = _run(tmp_path, ["--kill", CHAIN_ID])
    assert result.returncode == 0
    stub = result.stub_log
    assert f"--kill {RUN_ID_1}" not in stub  # already killed
    assert f"--kill {RUN_ID_2}" in stub


def test_kill_uuid_no_runs_is_ok(tmp_path: Path) -> None:
    """--kill <chain-id> with no matching live runs exits 0 cleanly."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    result = _run(tmp_path, ["--kill", CHAIN_ID])
    assert result.returncode == 0
    assert "no live runs found" in (result.stdout + result.stderr).lower()


def test_kill_non_uuid_falls_through(tmp_path: Path) -> None:
    """A non-UUID id falls through to the single-run kill path."""
    # No fixtures — single-run kill will fail looking for the run dir,
    # but the important assertion is that the chain dispatch does NOT
    # consume the arg.
    result = _run(tmp_path, ["--kill", "not-a-uuid"], use_self_stub=False)
    assert result.returncode != 0
    # The error should be about the run, not "no live runs found".
    out = result.stdout + result.stderr
    assert "no live runs found" not in out.lower()


# ---------------------------------------------------------------------------
# --resume <chain-id>
# ---------------------------------------------------------------------------


def test_resume_uuid_dispatches_paused_runs_only(tmp_path: Path) -> None:
    """--resume <chain-id> invokes single-run --resume for paused runs."""
    _fixture_two_run_chain(tmp_path)  # RUN_ID_1 paused, RUN_ID_2 pushed
    result = _run(tmp_path, ["--resume", CHAIN_ID])
    assert result.returncode == 0, result.stderr
    stub = result.stub_log
    # RUN_ID_1 is paused → resumed.
    assert f"--resume {RUN_ID_1}" in stub
    # RUN_ID_2 is pushed (done) → NOT resumed.
    assert f"--resume {RUN_ID_2}" not in stub


def test_resume_uuid_no_paused_runs_is_ok(tmp_path: Path) -> None:
    """No paused runs → exit 0 with a friendly message."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    _write_run(state_dir, RUN_ID_1, {
        "run_id": RUN_ID_1, "chain_id": CHAIN_ID, "wave_idx": 0,
        "pushed_at": "2026-06-14T01:00:00Z",
        "fly_machine_id": RUN_ID_1,
    })
    result = _run(tmp_path, ["--resume", CHAIN_ID])
    assert result.returncode == 0
    assert "no paused runs found" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# --stop <chain-id>
# ---------------------------------------------------------------------------


def test_stop_uuid_dispatches_running_runs(tmp_path: Path) -> None:
    """--stop <chain-id> invokes single-run --stop for runs that are
    actively running (have fly_machine_id, no terminal state)."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    state_dir.mkdir(parents=True)
    _write_run(state_dir, RUN_ID_1, {
        "run_id": RUN_ID_1, "chain_id": CHAIN_ID, "wave_idx": 0,
        "fly_machine_id": RUN_ID_1,
        # No pushed_at/paused_at/killed_at → still running.
    })
    _write_run(state_dir, RUN_ID_2, {
        "run_id": RUN_ID_2, "chain_id": CHAIN_ID, "wave_idx": 0,
        "fly_machine_id": RUN_ID_2,
        "pushed_at": "2026-06-14T01:00:00Z",
    })
    result = _run(tmp_path, ["--stop", CHAIN_ID])
    assert result.returncode == 0
    stub = result.stub_log
    assert f"--stop {RUN_ID_1}" in stub
    assert f"--stop {RUN_ID_2}" not in stub  # already pushed


# ---------------------------------------------------------------------------
# --finalize <chain-id>
# ---------------------------------------------------------------------------


def test_finalize_uuid_dispatches_unpushed_runs(tmp_path: Path) -> None:
    """--finalize <chain-id> invokes single-run --finalize for unpushed runs."""
    _fixture_two_run_chain(tmp_path)  # RUN_ID_1 paused (unpushed), RUN_ID_2 pushed
    result = _run(tmp_path, ["--finalize", CHAIN_ID])
    assert result.returncode == 0, result.stderr
    stub = result.stub_log
    assert f"--finalize {RUN_ID_1}" in stub
    # Already pushed → not finalized again.
    assert f"--finalize {RUN_ID_2}" not in stub
