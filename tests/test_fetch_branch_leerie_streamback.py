"""Tests for Step 4 of scripts/remote/fetch-branch.sh — stream-back of
/work/.leerie/config.toml and /work/.leerie/Dockerfile from a Fly Machine
to the host repo's .leerie/ directory.

This file owns the stream-back seam exclusively so it does not collide with
test_fetch_branch_sh.py's run-state/no_push assertions.  It re-uses the stub
helpers from that module (flyctl stub, git repo fixture) rather than
duplicating them.

Contracts under test:
  - Both config.toml and Dockerfile are copied when they exist on the machine
    and are absent on the host.
  - A pre-existing host file is never clobbered (per-file guard).
  - Absence of machine files is non-fatal: fetch_branch still returns 0.
  - LEERIE_STATE_HOST_DIR (when set) is used as the root instead of
    USER_REPO/.leerie.
  - Only one of the two files on the machine: the present one is streamed;
    the absent one is silently skipped; fetch_branch returns 0.
  - Both host files already exist: neither is overwritten; fetch_branch
    returns 0.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.test_fetch_branch_sh import (
    _make_fake_flyctl,
    _make_git_repo,
    _run_bash,
    FETCH_SH,
)

# ---------------------------------------------------------------------------
# Internal fixture helper
# ---------------------------------------------------------------------------

def _make_completed_run(
    tmp_path: Path, run_id: str
) -> tuple[Path, Path]:
    """Set up a completed run fixture (repo + machine state).

    Returns (repo_path, machine_runs_path).
    """
    repo = _make_git_repo(tmp_path)
    run_branch = f"leerie/runs/{run_id}"
    subprocess.run(
        ["git", "-C", str(repo), "branch", run_branch],
        check=True,
        capture_output=True,
    )
    machine_runs = tmp_path / "machine_runs"
    run_dir = machine_runs / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps({
            "finished_at": "2026-01-01T00:00:00Z",
            "branch": run_branch,
            "working_branch": "main",
        })
    )
    (run_dir / "state.json").write_text("{}")
    return repo, machine_runs


# ---------------------------------------------------------------------------
# Core stream-back tests
# ---------------------------------------------------------------------------

def test_streams_both_files_when_host_has_neither(tmp_path):
    """When config.toml and Dockerfile are on the machine but absent on the
    host, both are written to USER_REPO/.leerie/."""
    run_id = "sb-both-new-001"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text(
        "[config]\nsetup_packages = ['curl']\n"
    )
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\nRUN apt-get install -y curl\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-sb-both",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_leerie = repo / ".leerie"
    assert (host_leerie / "config.toml").exists(), "config.toml not written to host"
    assert (host_leerie / "Dockerfile").exists(), "Dockerfile not written to host"
    assert (host_leerie / "config.toml").read_text() == "[config]\nsetup_packages = ['curl']\n"
    assert (host_leerie / "Dockerfile").read_text() == "FROM debian:13\nRUN apt-get install -y curl\n"


def test_never_clobbers_existing_config_toml(tmp_path):
    """A host-side config.toml is never overwritten; the machine version is
    silently skipped even when it differs."""
    run_id = "sb-noclobber-cfg-002"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    original = "# hand-edited by operator\n[config]\nsetup_packages = ['git', 'curl']\n"
    (host_leerie / "config.toml").write_text(original)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = []\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-noclobber",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    # config.toml must be unchanged.
    assert (host_leerie / "config.toml").read_text() == original, (
        "Step 4 must not clobber a pre-existing host config.toml"
    )
    # Dockerfile was absent on the host — must be written.
    assert (host_leerie / "Dockerfile").exists(), (
        "Dockerfile (absent on host) must be streamed even when config.toml is skipped"
    )


def test_never_clobbers_existing_dockerfile(tmp_path):
    """A host-side Dockerfile is never overwritten, even when config.toml is absent."""
    run_id = "sb-noclobber-df-003"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    original_df = "FROM debian:13\n# custom operator layer\nRUN echo custom\n"
    (host_leerie / "Dockerfile").write_text(original_df)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = ['wget']\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-noclobber-df",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    # Dockerfile must be unchanged.
    assert (host_leerie / "Dockerfile").read_text() == original_df, (
        "Step 4 must not clobber a pre-existing host Dockerfile"
    )
    # config.toml was absent on the host — must be written.
    assert (host_leerie / "config.toml").exists(), (
        "config.toml (absent on host) must be streamed even when Dockerfile is skipped"
    )


def test_nonfatal_when_no_machine_leerie_files(tmp_path):
    """fetch_branch returns 0 when the machine has no .leerie/config.toml or
    .leerie/Dockerfile (dep_capture did not run or was skipped)."""
    run_id = "sb-nofatal-004"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    # No machine_leerie_dir — stub returns exit 1 for all test -f probes.
    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=None)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-nofatal",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, (
        "fetch_branch must return 0 when no .leerie/ files exist on machine; "
        f"stderr:\n{result.stderr}"
    )
    # Nothing should be written to the host .leerie/ root.
    host_leerie = repo / ".leerie"
    assert not (host_leerie / "config.toml").exists(), (
        "config.toml must not be created when machine probe returns absent"
    )
    assert not (host_leerie / "Dockerfile").exists(), (
        "Dockerfile must not be created when machine probe returns absent"
    )


def test_streams_only_present_machine_file(tmp_path):
    """When only one of the two files exists on the machine, only that file is
    written; the absent file is silently skipped and fetch_branch returns 0."""
    run_id = "sb-partial-005"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    # Only config.toml exists on the machine; Dockerfile is absent.
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = ['vim']\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-partial",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    host_leerie = repo / ".leerie"
    assert (host_leerie / "config.toml").exists(), "config.toml must be written"
    assert (host_leerie / "config.toml").read_text() == "[config]\nsetup_packages = ['vim']\n"
    # Dockerfile was absent on machine — must not be created on host.
    assert not (host_leerie / "Dockerfile").exists(), (
        "Dockerfile must not be created when it is absent on the machine"
    )


def test_skips_both_when_both_host_files_exist(tmp_path):
    """When both config.toml and Dockerfile already exist on the host, neither
    is overwritten and fetch_branch still returns 0."""
    run_id = "sb-both-exist-006"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    cfg_content = "[config]\nsetup_packages = ['existing']\n"
    df_content = "FROM debian:13\n# existing\n"
    (host_leerie / "config.toml").write_text(cfg_content)
    (host_leerie / "Dockerfile").write_text(df_content)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = []\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-both-exist",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    # Both files must be unchanged.
    assert (host_leerie / "config.toml").read_text() == cfg_content
    assert (host_leerie / "Dockerfile").read_text() == df_content


def test_writes_to_leerie_state_host_dir_when_set(tmp_path):
    """When LEERIE_STATE_HOST_DIR is set, files land there instead of
    USER_REPO/.leerie/."""
    run_id = "sb-hostdir-007"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)

    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text("[config]\nsetup_packages = ['jq']\n")
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\nRUN apt-get install -y jq\n")

    _make_fake_flyctl(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    custom_root = tmp_path / "custom_state_root"
    custom_root.mkdir()

    result = _run_bash(
        f"source {FETCH_SH}; fetch_branch",
        env={
            "LEERIE_MACHINE_ID": "m-hostdir",
            "LEERIE_FLY_APP": "leerie",
            "USER_REPO": str(repo),
            "LEERIE_STATE_HOST_DIR": str(custom_root),
            "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        },
    )
    assert result.returncode == 0, f"stderr:\n{result.stderr}"

    assert (custom_root / "config.toml").exists(), (
        "config.toml must be written under LEERIE_STATE_HOST_DIR"
    )
    assert (custom_root / "Dockerfile").exists(), (
        "Dockerfile must be written under LEERIE_STATE_HOST_DIR"
    )
    # Nothing must land in USER_REPO/.leerie.
    assert not (repo / ".leerie" / "config.toml").exists(), (
        "config.toml must NOT be written under USER_REPO/.leerie "
        "when LEERIE_STATE_HOST_DIR is set"
    )
    assert not (repo / ".leerie" / "Dockerfile").exists(), (
        "Dockerfile must NOT be written under USER_REPO/.leerie "
        "when LEERIE_STATE_HOST_DIR is set"
    )
