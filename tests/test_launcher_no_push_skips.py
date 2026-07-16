"""Tests for the launcher's `run.json.no_push` honoring check.

When phase_finalize or `_finish_no_work_run` (DESIGN §8 *The
cleared-but-empty terminal state*) writes `no_push=true` to run.json,
the host launcher must skip its `git push` + `gh pr create` step. The
no-work case is the load-bearing one: no run branch was ever
materialized, so attempting `git push` would error with "src refspec
does not match any" and the launcher would write `push_error` to
run.json — turning a successful no-op into a `push-failed` row in
`leerie --list`.

The check lives entirely in the bash launcher (`leerie`), so this test
invokes a minimal bash harness that mirrors the exact block from the
launcher's host-side finalize step. Analogous to
`test_finalize_sh_behavior.py`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

# The harness below mirrors a host-side launcher block that reads run.json
# with real `jq` (see the _HARNESS comment). Host-only; see
# `tests/conftest.py`'s HAS_JQ.
pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Minimal bash harness mirroring the `leerie:1207-1216` block. The
# launcher reads run.json with jq; the harness does the same against
# the run dir the test set up. Echoes "SKIPPED" + exits 0 on the
# no_push path; echoes "PROCEED" + exits 0 when the check would fall
# through to the (omitted) push step.
_HARNESS = """\
#!/usr/bin/env bash
set -euo pipefail
LATEST_RUN_DIR="$1"

if [ "$(jq -r '.no_push // false' "$LATEST_RUN_DIR/run.json")" = "true" ]; then
  echo "[leerie] finalize: run.json has no_push=true; skipping push + PR"
  exit 0
fi

# Stand-in for the real launcher's push logic. If we get here the
# real launcher would attempt `git push -u origin "$RUN_BRANCH"`.
echo "PROCEED"
exit 0
"""


def _run(run_dir: Path) -> tuple[int, str]:
    """Run the harness and return (returncode, combined stdout+stderr)."""
    result = subprocess.run(
        ["bash", "-c", _HARNESS, "--", str(run_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


def _write_run_json(run_dir: Path, **fields) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(fields))


def test_no_push_true_skips_push(tmp_path):
    """run.json.no_push == true → harness skips push, exits 0, logs
    the skip line. This is the load-bearing case for the no-work path
    (no run branch was materialized; trying to push would fail)."""
    run_dir = tmp_path / "runs" / "bugfix-already-done-abc123"
    _write_run_json(
        run_dir,
        finished_at="2026-05-31T20:00:00Z",
        no_push=True,
        branch="leerie/runs/bugfix-already-done-abc123",
        working_branch="main",
    )
    rc, output = _run(run_dir)
    assert rc == 0
    assert "skipping push + PR" in output
    assert "PROCEED" not in output


def test_no_push_false_proceeds_to_push(tmp_path):
    """run.json.no_push == false → harness falls through to the push
    step (normal finalize, user did NOT pass --no-push)."""
    run_dir = tmp_path / "runs" / "feat-something-def456"
    _write_run_json(
        run_dir,
        finished_at="2026-05-31T20:00:00Z",
        no_push=False,
        branch="leerie/runs/feat-something-def456",
        working_branch="main",
    )
    rc, output = _run(run_dir)
    assert rc == 0
    assert "PROCEED" in output
    assert "skipping" not in output


def test_no_push_absent_proceeds_to_push(tmp_path):
    """run.json without a no_push field at all → defaults to false
    via `jq -r '.no_push // false'`, falls through to the push step.
    Backwards-compat guard for run.json sidecars written by earlier
    versions of leerie that did not write the field."""
    run_dir = tmp_path / "runs" / "legacy-run-no-field"
    _write_run_json(
        run_dir,
        finished_at="2026-05-31T20:00:00Z",
        branch="leerie/runs/legacy-run-no-field",
        working_branch="main",
    )
    rc, output = _run(run_dir)
    assert rc == 0
    assert "PROCEED" in output
    assert "skipping" not in output


def test_no_push_null_proceeds_to_push(tmp_path):
    """run.json with no_push: null (explicit null, e.g. from an
    `update_run_json` call that cleared a prior value) defaults to
    false. Same jq filter handles both absent and null."""
    run_dir = tmp_path / "runs" / "nulled-no-push"
    _write_run_json(
        run_dir,
        finished_at="2026-05-31T20:00:00Z",
        no_push=None,
        branch="leerie/runs/nulled-no-push",
        working_branch="main",
    )
    rc, output = _run(run_dir)
    assert rc == 0
    assert "PROCEED" in output


# ----- source-text coupling: the harness must match the real launcher ------

def test_launcher_leerie_contains_the_no_push_check():
    """Pin the host-finalize source so a refactor that removes or
    rewords the no_push check fails THIS test instead of silently
    breaking the no-work flow. Mirrors the pattern in
    test_finalize_sh_behavior.py and test_orchestrate_call_sites.py.

    Note: the no_push check moved from `leerie` (inline at finalize) into
    `scripts/host-finalize.sh` (the extracted host_finalize function)
    as part of Audit Drift 7. The pin now targets that file."""
    host_finalize = (REPO_ROOT / "scripts" / "host-finalize.sh").read_text()
    # The jq invocation in the extracted host_finalize function.
    assert (
        '$(jq -r \'.no_push // false\' "$run_json")'
        in host_finalize
    ), (
        "host_finalize must read `.no_push` from run.json and honor "
        "true. Without it, a no-work leerie run (DESIGN §8) tries to "
        "push a non-existent branch and the launcher writes "
        "push_error to run.json."
    )
    # And the skip+exit must be on the true branch.
    assert (
        "skipping push + PR" in host_finalize
    ), "the launcher's no_push skip must log a recognizable message"
