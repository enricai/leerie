"""Tests for `leerie --finalize <final-id>` when only the bootstrap dir
exists locally (P2a fallback).

When a Fly run dies before its host-side sync (ENOSPC, worker crash,
etc.), only the bootstrap dir `_bootstrap-<6hex>/` exists on the host —
the final-id dir is never created. Pre-fix behavior: passing the final
id to `--finalize` errored with "no run dir at ..." and the user had to
know to pass the bootstrap id instead. Post-fix: the launcher scans for
a sibling `_bootstrap-*` dir with `fly-machine.json`, re-binds
`_fin_run_dir` to it, and proceeds — the existing `LEERIE_REMOTE_RUN_ID`
plumbing migrates the dir after `fetch_branch` discovers the actual
final id on the machine.

These tests cover only the host-side resolver logic (do we pick the
right run dir before any SSH attempt). The full SSH + fetch_branch
exercise lives in tests/test_force_finalize_sh.py and the existing
fetch_branch tests; here we assert the dispatch decision.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"


def _make_user_repo(tmp_path: Path) -> Path:
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    subprocess.run(["git", "-C", str(user_repo), "init", "-b", "main"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(user_repo), "config", "user.email", "t@t.com"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(user_repo), "config", "user.name", "T"],
                   check=True, capture_output=True)
    (user_repo / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(user_repo), "add", "README.md"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(user_repo), "commit", "-m", "init"],
                   check=True, capture_output=True)
    return user_repo


def _make_bootstrap_dir(state_dir: Path, bootstrap_id: str,
                        machine_id: str = "7812445c537208") -> Path:
    """Synthesize a bootstrap dir with fly-machine.json, no run.json.
    Creates the dir under state_dir/runs/ (LEERIE_STATE_HOST_DIR layout)."""
    bs_dir = state_dir / "runs" / bootstrap_id
    bs_dir.mkdir(parents=True)
    (bs_dir / "fly-machine.json").write_text(json.dumps({
        "fly_app": "leerie",
        "fly_machine_id": machine_id,
        "started_at": "2026-06-02T23:24:06+00:00",
        "run_id": bootstrap_id,
        "launcher_pid": 60906,
        "host_no_push": False,
    }, indent=2))
    return bs_dir


def _make_fake_flyctl_that_fails(tmp_path: Path) -> Path:
    """Stub flyctl that satisfies `auth status` but fails any actual
    machine command — we only need to verify the resolver path got
    far enough to attempt the SSH; we don't want it to actually run."""
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    auth) shift; case "${1:-}" in status) exit 0 ;; esac ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        # Any unrecognized command fails — we want the test to short-
        # circuit before any actual ssh.
        'exit 2\n'
    )
    stub.chmod(0o755)
    return stub


def test_falls_back_to_bootstrap_when_only_bootstrap_exists(tmp_path):
    """User passes the final id `feat-foo-abc123` but only
    `_bootstrap-5de324/` exists locally. The resolver should pick up
    the bootstrap dir, log the fallback, and proceed (failing later
    on the stubbed flyctl). The key assertion is that we DIDN'T
    error out with 'no run dir' — we got far enough to attempt
    fetch_branch."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    bs_id = "_bootstrap-5de324"
    _make_bootstrap_dir(state_dir, bs_id)
    stub = _make_fake_flyctl_that_fails(tmp_path)
    final_id = "feat-cool-redesign-abc123"
    env = {
        **os.environ,
        "PATH": f"{stub.parent}:{os.environ.get('PATH', '')}",
        "LEERIE_STATE_DIR": str(state_dir),
    }
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", final_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env=env,
    )
    combined = result.stdout + result.stderr
    # Old broken path: "no run dir at .../feat-cool-redesign-abc123"
    # should NOT appear — the fallback caught it.
    assert "no run dir at" not in combined, (
        "Fallback should have caught the missing final-id dir.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    # The fallback log line should mention picking up the bootstrap dir.
    assert "using bootstrap dir" in combined, (
        "Expected fallback log line mentioning the bootstrap dir.\n"
        f"stderr:\n{result.stderr}"
    )
    assert bs_id in combined, (
        f"Expected the bootstrap id {bs_id} to appear in the log."
    )


def test_errors_when_no_bootstrap_and_no_final(tmp_path):
    """When neither the final-id dir nor any bootstrap dir exists,
    --finalize errors with the original 'no run dir' message plus the
    new `leerie --list` hint."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    # No bootstrap dir, no final-id dir.
    final_id = "feat-cool-redesign-abc123"
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", final_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "LEERIE_STATE_DIR": str(state_dir)},
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "no run dir at" in combined, combined
    assert "leerie --list" in combined, (
        "New error message should suggest `leerie --list`."
    )


def test_errors_on_multiple_bootstrap_dirs(tmp_path):
    """When the final-id dir is missing but multiple bootstrap dirs
    exist, the resolver should refuse to guess and fail clearly."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    _make_bootstrap_dir(state_dir, "_bootstrap-aaaaaa", "machine-aaa")
    _make_bootstrap_dir(state_dir, "_bootstrap-bbbbbb", "machine-bbb")
    final_id = "feat-ambiguous-cccccc"
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", final_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "LEERIE_STATE_DIR": str(state_dir)},
    )
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    # Both bootstrap ids should be listed for the user to disambiguate.
    assert "_bootstrap-aaaaaa" in combined and "_bootstrap-bbbbbb" in combined, (
        "Multi-bootstrap error should list all candidates.\n"
        f"stderr:\n{result.stderr}"
    )


def test_final_id_passes_through_when_final_dir_exists(tmp_path):
    """Sanity: when the final-id dir DOES exist locally (the normal
    auto-sync path completed), the fallback path is skipped entirely
    and the legacy resolver consumes the final-id dir directly."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    final_id = "feat-normal-flow-ddd111"
    final_dir = state_dir / "runs" / final_id
    final_dir.mkdir(parents=True)
    # Provide a complete sidecar so the short-circuit (pushed_at set →
    # already-pushed) fires and we don't fall into the SSH path. This
    # isolates the new resolver from network behavior.
    (final_dir / "run.json").write_text(json.dumps({
        "branch": f"leerie/runs/{final_id}",
        "working_branch": "main",
        "finished_at": "2026-06-02T19:00:00+00:00",
        "pushed_at": "2026-06-02T19:05:00+00:00",
    }))
    (final_dir / "state.json").write_text("{}")
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", final_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "LEERIE_STATE_DIR": str(state_dir)},
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    # Fallback should NOT have fired.
    assert "using bootstrap dir" not in combined, (
        "Fallback was not supposed to fire when the final-id dir "
        "already exists.\nstderr:\n" + result.stderr
    )
    assert "already pushed" in combined
