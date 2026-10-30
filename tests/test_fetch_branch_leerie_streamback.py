"""Tests for Step 4 of scripts/remote/fetch-branch.sh: the run's recorded
dependency captures (`<run-dir>/captured-deps.json`, streamed home with the
run directory) are applied to the user's `.leerie/config.toml` on the host
(DESIGN §6½ *The host applies a capture*), and `/work/.leerie/Dockerfile` is
streamed back from the Fly Machine. The machine's `.leerie/config.toml` is
no longer streamed: the container records captures, never writes config.

This file owns the stream-back seam exclusively so it does not collide with
test_fetch_branch_sh.py's run-state/no_push assertions.  It re-uses the stub
helpers from that module (flyctl stub, git repo fixture) rather than
duplicating them.

Contracts under test:
  - The capture is merged into the user's config (a hand-edited key is kept
    and added to), and the Dockerfile is streamed when absent on the host.
  - A pre-existing host Dockerfile is never clobbered.
  - Absence of machine files and of a capture is non-fatal: fetch_branch
    still returns 0 and writes nothing.
  - A config.toml on the machine is not streamed.
  - LEERIE_STATE_HOST_DIR (when set) is the root for the run directory and
    the Dockerfile; the capture still goes to the user's checkout.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import run_bash
from tests.test_fetch_branch_sh import (
    _flyctl_stub,
    _make_git_repo,
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


REPO_ROOT = Path(__file__).resolve().parent.parent


def _capture(machine_runs: Path, run_id: str, pkgs: list[str]) -> None:
    """The run's recorded capture, as the orchestrator leaves it in the run
    directory on the machine (`_record_capture`)."""
    (machine_runs / run_id / "captured-deps.json").write_text(json.dumps([
        {"from_run": run_id, "setup_packages": pkgs,
         "language_installs": [], "replace": False}]))


def _fetch(tmp_path: Path, repo: Path, machine: str, **extra: str):
    env = {"LEERIE_MACHINE_ID": machine, "LEERIE_FLY_APP": "leerie",
           "USER_REPO": str(repo), "LEERIE_REPO": str(REPO_ROOT),
           "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin", **extra}
    return run_bash(f"source {FETCH_SH}; fetch_branch", env=env)


def _setup_packages(repo: Path) -> str | None:
    import re
    cfg = repo / ".leerie" / "config.toml"
    if not cfg.is_file():
        return None
    m = re.search(r'^setup_packages = "(.*)"$', cfg.read_text(), re.M)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Core stream-back tests
# ---------------------------------------------------------------------------

def test_applies_the_capture_and_streams_the_dockerfile(tmp_path):
    run_id = "sb-both-new-001"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    _capture(machine_runs, run_id, ["curl"])
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text('setup_packages = "stale"\n')
    (machine_leerie / "Dockerfile").write_text(
        "FROM debian:13\nRUN apt-get install -y curl\n")
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _fetch(tmp_path, repo, "m-sb-both")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert _setup_packages(repo) == "curl"
    assert (repo / ".leerie" / "Dockerfile").read_text() == (
        "FROM debian:13\nRUN apt-get install -y curl\n")
    assert not (repo / ".leerie" / "runs" / run_id / "captured-deps.json").exists()


def test_the_capture_merges_with_a_hand_edited_config(tmp_path):
    run_id = "sb-noclobber-cfg-002"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    (host_leerie / "config.toml").write_text(
        '# hand-edited by operator\nsetup_packages = "git curl"\n')
    _capture(machine_runs, run_id, ["wget"])
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=None)

    result = _fetch(tmp_path, repo, "m-noclobber")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    text = (host_leerie / "config.toml").read_text()
    assert "# hand-edited by operator" in text
    assert _setup_packages(repo) == "git curl wget"


def test_never_clobbers_existing_dockerfile(tmp_path):
    run_id = "sb-noclobber-df-003"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    host_leerie = repo / ".leerie"
    host_leerie.mkdir()
    original_df = "FROM debian:13\n# custom operator layer\nRUN echo custom\n"
    (host_leerie / "Dockerfile").write_text(original_df)
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "Dockerfile").write_text("FROM debian:13\n")
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _fetch(tmp_path, repo, "m-noclobber-df")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert (host_leerie / "Dockerfile").read_text() == original_df


def test_nonfatal_when_no_machine_leerie_files(tmp_path):
    run_id = "sb-nofatal-004"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=None)

    result = _fetch(tmp_path, repo, "m-nofatal")
    assert result.returncode == 0, (
        "fetch_branch must return 0 when no .leerie/ files exist on machine; "
        f"stderr:\n{result.stderr}")
    assert not (repo / ".leerie" / "config.toml").exists()
    assert not (repo / ".leerie" / "Dockerfile").exists()


def test_a_machine_config_toml_is_not_streamed(tmp_path):
    """The container records captures and never writes config; a config.toml
    on the machine (an older run's) does not reach the host."""
    run_id = "sb-partial-005"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "config.toml").write_text('setup_packages = "vim"\n')
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)

    result = _fetch(tmp_path, repo, "m-partial")
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert not (repo / ".leerie" / "config.toml").exists()
    assert not (repo / ".leerie" / "Dockerfile").exists()


def test_state_host_dir_takes_the_run_and_dockerfile_not_the_capture(tmp_path):
    run_id = "sb-hostdir-007"
    repo, machine_runs = _make_completed_run(tmp_path, run_id)
    _capture(machine_runs, run_id, ["jq"])
    machine_leerie = tmp_path / "machine_leerie"
    machine_leerie.mkdir()
    (machine_leerie / "Dockerfile").write_text(
        "FROM debian:13\nRUN apt-get install -y jq\n")
    _flyctl_stub(tmp_path, machine_runs, repo, machine_leerie_dir=machine_leerie)
    custom_root = tmp_path / "custom_state_root"
    custom_root.mkdir()

    result = _fetch(tmp_path, repo, "m-hostdir",
                    LEERIE_STATE_HOST_DIR=str(custom_root))
    assert result.returncode == 0, f"stderr:\n{result.stderr}"
    assert (custom_root / "Dockerfile").exists()
    assert (custom_root / "runs" / run_id / "captured-deps.applied.json").exists()
    # The capture goes to the user's checkout, the config's one home.
    assert _setup_packages(repo) == "jq"
    assert not (custom_root / "config.toml").exists()
