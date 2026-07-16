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

import pytest

from tests.conftest import HAS_JQ

# Exercises the launcher's own `--finalize` path, which is host-side and reads
# run.json with real `jq` — the launcher in fact hard-fails at preflight when
# jq is missing. See `tests/conftest.py`'s HAS_JQ.
pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

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
    """When pushed_at is set and the run branch is ABSENT locally (a Fly
    run not yet fetched, or a no-work run — nothing local to compare or
    re-push), --finalize keeps the original idempotent "already pushed"
    short-circuit (exit 0). The tip-aware gate (DESIGN §6 *Finalization*)
    only falls through to re-finalize when the branch is present locally
    AND origin is behind it; branch-absent must NOT regress into a
    pointless re-fetch."""
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


def test_finalize_repushes_when_local_branch_ahead_of_origin(tmp_path):
    """Partial-push recovery (DESIGN §6 *Finalization*, the PR-#22 wedge):
    when pushed_at is set but the run branch is present locally and AHEAD
    of origin (a prior finalize pushed a partial branch), --finalize must
    NOT short-circuit — it falls through to re-finalize so host_finalize
    can re-push the complete branch. Here origin has no such ref at all
    (the partial push was rewound / never landed the full branch), which
    is the exact shape of the incident run."""
    user_repo = _make_user_repo(tmp_path)
    # Create the run branch locally so rev-parse --verify succeeds and it
    # is ahead of (absent) origin — origin has no matching ref.
    run_id = "feat-partial-pushed-def456"
    branch = f"leerie/runs/{run_id}"
    subprocess.run(["git", "-C", str(user_repo), "branch", branch],
                   check=True, capture_output=True)
    state_dir = tmp_path / "leerie-state"
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "branch": branch,
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
    combined = result.stdout + result.stderr
    # Must NOT take the idempotent short-circuit — it must announce the
    # re-finalize instead.
    assert "is already pushed; nothing to do" not in combined, combined
    assert "origin is not up to date (partial prior push)" in combined, combined


# --- Strict argparse: typos and extra positionals fail loudly --------------
#
# Regression cover for the silent-typo class where the launcher's
# `--finalize` argparse (leerie:682–717) accepted any unknown `--*`
# flag without error. The original incident: a user ran
# `leerie --finalize <id> --foorce` (typo for --force), the launcher
# silently dropped --foorce, _FINALIZE_FORCE stayed false,
# force_finalize_remote was never invoked, fetch_branch correctly
# reported "no completed unpushed run on machine," and the user thought
# their recovery command had failed for an unrelated reason. The fix
# adds a `--*) error; exit 1` catch-all plus a strict positional check.

def test_finalize_rejects_unknown_flag(tmp_path):
    """`leerie --finalize <id> --foorce` exits non-zero with a clear
    'unknown flag' error and the Usage line — instead of silently
    dropping the typo and taking the non-force fetch path."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    state_dir.mkdir()
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", "some-run-id", "--foorce"],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )
    assert result.returncode != 0, (
        f"--finalize should reject unknown flag --foorce.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "unknown flag: --foorce" in combined, (
        "error message should name the unknown flag.\n"
        f"stderr:\n{result.stderr}"
    )


def test_finalize_accepts_force_correctly_spelled(tmp_path):
    """Sanity check: --force (correct spelling) is still recognized.
    Guards against an over-aggressive catch-all that would reject the
    real flag."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    state_dir.mkdir()
    # Pass a run-id that doesn't exist locally to make --finalize fail
    # after argparse but before any remote call. The point is to confirm
    # argparse does not reject --force itself.
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", "nonexistent-run", "--force"],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )
    # Failure is expected (no such run), but the error must NOT be
    # "unknown flag" — the flag is valid.
    combined = result.stdout + result.stderr
    assert "unknown flag: --force" not in combined, (
        "--force must be a recognized flag.\n"
        f"stderr:\n{result.stderr}"
    )


def test_finalize_rejects_extra_positional(tmp_path):
    """`leerie --finalize id1 id2` — the second positional argument is
    not a recognized flag and shouldn't be silently ignored. Previously
    the argparse loop would have iterated past `id2` as just-another-arg
    that doesn't match the case statement."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    state_dir.mkdir()
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", "run-id-1", "run-id-2"],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )
    assert result.returncode != 0, (
        f"--finalize should reject an extra positional argument.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "unexpected extra positional" in combined, (
        "error message should call out the extra positional.\n"
        f"stderr:\n{result.stderr}"
    )


def test_finalize_rejects_dangling_runtime_flag(tmp_path):
    """`leerie --finalize <id> --runtime` (no value after the flag) is
    a partial invocation — without this guard, the _fin_prev state
    machine leaves _fin_runtime empty, the downstream
    [ -n "$_fin_runtime" ] validator passes, and the launcher silently
    proceeds with the default Fly path. The 7a41153 "strict argparse"
    change caught --foorce-style typos but missed this case; this
    test guards the post-loop _fin_prev check that closes it."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    state_dir.mkdir()
    result = subprocess.run(
        ["bash", str(LEERIE), "--finalize", "some-run-id", "--runtime"],
        cwd=str(user_repo),
        capture_output=True, text=True,
        env={**os.environ, "PATH": os.environ.get("PATH", ""),
             "LEERIE_STATE_DIR": str(state_dir)},
    )
    assert result.returncode != 0, (
        f"--finalize should reject a dangling --runtime flag.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "--runtime requires a value" in combined, (
        "error message should explain that --runtime needs a value.\n"
        f"stderr:\n{result.stderr}"
    )


def test_finalize_accepts_no_verify_and_no_push(tmp_path):
    """Sanity check: --no-verify and --no-push are valid --finalize
    flags (consumed by the post-fetch block at leerie:899–906) and must
    pass the strict argparse without 'unknown flag' errors."""
    user_repo = _make_user_repo(tmp_path)
    state_dir = tmp_path / "leerie-state"
    state_dir.mkdir()
    for flag in ("--no-verify", "--no-push"):
        result = subprocess.run(
            ["bash", str(LEERIE), "--finalize", "nonexistent-run", flag],
            cwd=str(user_repo),
            capture_output=True, text=True,
            env={**os.environ, "PATH": os.environ.get("PATH", ""),
                 "LEERIE_STATE_DIR": str(state_dir)},
        )
        combined = result.stdout + result.stderr
        assert f"unknown flag: {flag}" not in combined, (
            f"{flag} must be a recognized --finalize flag.\n"
            f"stderr:\n{result.stderr}"
        )
