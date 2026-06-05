"""Tests for `leerie --finalize <run-id>` against a no-work run.

Regression cover for the bug where the launcher's `--finalize`
recovery path could not finalize a cleared-but-empty terminal-state
run (DESIGN §8). For such runs `_finish_no_work_run` writes
`no_push=true` to run.json on the Fly machine; the auto-sync path
should preserve that intent host-side, and `--finalize` should
recognize the run as already synced without requiring a local run
branch.

The launcher's `--finalize` handler is a case-statement arm that
runs before any container/runtime setup (leerie:127), so it can be
exercised directly with `bash leerie --finalize <id>` against a
synthesized host-side run dir.

The companion bash-harness tests cover the script-level mechanics:
- `tests/test_host_finalize_sh.py::test_skips_when_run_branch_absent_locally`
  for Fix C (host_finalize rev-parse defense in depth).
- `tests/test_fetch_branch_sh.py::test_fetch_branch_skips_bundle_when_branch_missing`
  for Fix A (fetch-branch.sh's conditional stripper preserves intent).

This test focuses on Fix B (the --finalize arm of `leerie` itself).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"


def _make_user_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo to act as $USER_REPO. The repo has
    one commit on `main` but does NOT have any `leerie/runs/...`
    branch — the no-work shape we're regression-covering."""
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


def _make_no_work_run_dir(state_dir: Path, run_id: str) -> Path:
    """Synthesize the host-side artifacts of a cleared-but-empty
    terminal-state run that has been auto-synced back to the host:
      - run.json with finished_at, no_push=true, branch, working_branch
      - state.json with no_work_required=true
      - NO local `leerie/runs/<id>` branch (the run never created one)
    Creates under state_dir/runs/ (LEERIE_STATE_HOST_DIR layout).
    """
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
        "working_branch": "main",
        "started_at": "2026-06-02T19:42:21+00:00",
        "finished_at": "2026-06-02T19:44:46+00:00",
        "no_push": True,
        "task": "no-work regression test",
    }))
    (run_dir / "state.json").write_text(json.dumps({
        "task": "no-work regression test",
        "no_work_required": True,
        "finished_at": "2026-06-02T19:44:46+00:00",
        "waves": [],
        "subtask_status": {},
    }))
    return run_dir


def test_finalize_no_work_run_exits_cleanly(tmp_path):
    """The user's exact failure mode: `leerie --finalize <id>` on a
    no-work run that auto-synced to host should exit 0 cleanly,
    recognize it as already synced (Fix B1), preserve no_push=true
    (Fix B2), and short-circuit host_finalize on the no_push gate.

    Without Fix B1, --finalize takes the `else` arm at leerie:423 and
    dies with "no fly_machine_id and is not already synced."
    Without Fix B2, the stripper at leerie:499-533 would clear
    no_push=true, host_finalize would attempt `git push` on the
    non-existent branch, and the run would fail with `src refspec ...
    does not match any`."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    run_id = "bugfix-already-done-test-c838cc"
    _make_no_work_run_dir(state_dir, run_id)

    # Run from inside the user repo, as the user does.
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", run_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )

    assert result.returncode == 0, (
        f"--finalize on a no-work run should exit 0.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    # Fix B1: recognized as already-synced; fetch_branch is skipped.
    assert "already synced to host" in combined, (
        "--finalize should recognize the no-work run as already "
        "synced (Fix B1).\nstderr:\n" + result.stderr
    )
    # host_finalize hits the no_push gate at host-finalize.sh:70.
    assert "no_push=true" in combined and "skipping push + PR" in combined, (
        "host_finalize should short-circuit on the preserved "
        "no_push=true intent.\nstderr:\n" + result.stderr
    )
    # The dead-end message must NOT appear.
    assert "no fly_machine_id and is not already synced" not in combined, (
        "The old dead-end branch must not fire for no-work runs."
    )
    # And the run.json must still carry no_push=true (Fix B2 — the
    # stripper at lines 499-533 must have been skipped because the
    # branch is absent locally).
    after = json.loads(
        (state_dir / "runs" / run_id / "run.json").read_text()
    )
    assert after.get("no_push") is True, (
        "Fix B2: no_push=true must be preserved when the run branch "
        "is absent locally. Found: " + json.dumps(after, indent=2)
    )
    # And nothing was pushed.
    assert after.get("pushed_at") is None
    assert after.get("push_error") is None


def test_finalize_already_pushed_short_circuits(tmp_path):
    """Sanity check: when pushed_at is already set, --finalize prints
    the existing idempotent "already pushed" message regardless of
    the branch state. Confirms Fix B didn't disturb the earlier
    short-circuit at leerie:348-360."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    run_id = "feat-already-pushed-abc123"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "branch": f"leerie/runs/{run_id}",
        "working_branch": "main",
        "finished_at": "2026-06-02T19:00:00+00:00",
        "pushed_at": "2026-06-02T19:05:00+00:00",
    }))
    (run_dir / "state.json").write_text("{}")

    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", run_id],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )
    assert result.returncode == 0, result.stderr
    assert "already pushed" in result.stderr or "already pushed" in result.stdout
