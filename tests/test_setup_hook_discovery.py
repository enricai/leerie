"""Tests for `_run_setup_hook` — the optional `.leerie-setup.sh` escape
hatch for repos that need system packages a base image can't reasonably
ship (Postgres client, ImageMagick, etc.).

The hook is repo-owned: its trust model is identical to any other file
committed to the repo. The orchestrator runs it if present, before mise
install + lockfile detection (DESIGN §6½).

Idempotency is enforced via `st.data["provision"]["sh_hook_ran"]` so a
re-entry into `_run_setup_hook` from a recovery path does not re-fire
the script.
"""
from __future__ import annotations

import asyncio
import os

import pytest


def _make_state(leerie, tmp_path):
    """Build a minimal State at tmp_path/runs/<id>/state.json."""
    leerie_root = tmp_path / ".leerie"
    run_id = "_test-run"
    (leerie_root / "runs" / run_id / "logs").mkdir(parents=True, exist_ok=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test"}
    st.save()
    return st


def test_no_hook_is_a_silent_noop(leerie, tmp_path):
    """A repo without `.leerie-setup.sh` runs the helper without effect.
    sh_hook_ran is NOT set (no run occurred to be idempotent about)."""
    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"
    asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))
    assert "provision" not in st.data or \
        not st.data["provision"].get("sh_hook_ran")


def test_hook_path_is_a_directory_dies_with_clear_message(leerie, tmp_path):
    """If `.leerie-setup.sh` exists at the repo root but is a directory
    (most likely committed by mistake), the helper must NOT silently
    skip — workers would later fail with confusing missing-system-
    package errors. Surface the misshape immediately with a clear
    `die()` message."""
    (tmp_path / ".leerie-setup.sh").mkdir()
    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))
    # No partial state should have been recorded.
    assert not st.data.get("provision", {}).get("sh_hook_ran")


def test_present_hook_runs_and_sets_idempotent_flag(leerie, tmp_path):
    """A hook that exits 0 marks sh_hook_ran=True and persists state."""
    hook = tmp_path / ".leerie-setup.sh"
    marker = tmp_path / "ran.marker"
    hook.write_text(f"#!/usr/bin/env bash\ntouch {marker}\n")
    os.chmod(hook, 0o755)

    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"
    asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))

    assert marker.exists(), "hook script did not execute"
    assert st.data["provision"]["sh_hook_ran"] is True


def test_second_call_is_idempotent(leerie, tmp_path):
    """If sh_hook_ran is already True, the helper does NOT re-execute
    the script. This is the resume-safety property."""
    hook = tmp_path / ".leerie-setup.sh"
    counter_file = tmp_path / "count"
    counter_file.write_text("0")
    hook.write_text(
        f"#!/usr/bin/env bash\n"
        f"echo $(( $(cat {counter_file}) + 1 )) > {counter_file}\n"
    )
    os.chmod(hook, 0o755)

    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"

    asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))
    assert counter_file.read_text().strip() == "1"

    # Second call: should NOT increment.
    asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))
    assert counter_file.read_text().strip() == "1"


def test_nonzero_exit_aborts_via_die(leerie, tmp_path):
    """A hook that exits non-zero calls `die()`, which raises SystemExit.
    sh_hook_ran is NOT set so a recovery re-run sees the same precondition."""
    hook = tmp_path / ".leerie-setup.sh"
    hook.write_text("#!/usr/bin/env bash\necho boom >&2\nexit 7\n")
    os.chmod(hook, 0o755)

    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"

    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))
    assert not st.data.get("provision", {}).get("sh_hook_ran")


def test_hook_output_is_logged(leerie, tmp_path):
    """The helper writes combined stdout+stderr to
    <log_dir>/setup-hook.log so the user can inspect what happened."""
    hook = tmp_path / ".leerie-setup.sh"
    hook.write_text("#!/usr/bin/env bash\necho hello-from-hook\n")
    os.chmod(hook, 0o755)

    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"
    asyncio.run(leerie._run_setup_hook(tmp_path, log_dir, st))

    log_path = log_dir / "setup-hook.log"
    assert log_path.exists()
    assert "hello-from-hook" in log_path.read_text()
