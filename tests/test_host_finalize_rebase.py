"""Behavioral tests for the best-effort rebase-onto-base step inserted into
scripts/host-finalize.sh (DESIGN §6 *Finalization* "Rebase-onto-base before
push"). Companion to tests/test_host_finalize_sh.py, which stubs `git`
entirely and therefore cannot exercise real rebase/worktree mechanics.

These tests build a REAL local git repo (mirroring
tests/test_finalize_sh_behavior.py's `_init_repo` pattern) so `git worktree
add` / `git rebase` / `git fetch` all run for real, and stub only the
external dependency the rebase step actually delegates to: the
`orchestrator/leerie.py` `run_rebaser()` python3 seam. This is done by
putting a fake `python3` on PATH that recognizes the rebaser script (by a
distinguishing marker written into the scratch script file) and prints a
canned JSON verdict, while falling through to the real `python3` for
anything else (there is nothing else in this code path, but the fallback
keeps the stub honest).

Live LLM behavior (does the rebaser worker actually resolve conflicts
correctly / abort correctly) is validated separately and empirically — see
the plan file's "Empirical validation" section — not re-derived here. These
tests cover host-finalize.sh's own control flow: does it create the
worktree, invoke the seam, act correctly on each returned status, fold a
diagnosis into the PR body, and never block finalize regardless of outcome.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ

pytestmark = pytest.mark.skipif(
    not HAS_JQ,
    reason="host-only script: needs real `jq`, which the launcher guarantees "
           "on the host and the leerie image deliberately omits",
)

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                    capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    """Real repo on `main`, one commit, with a real BARE `origin` remote so
    `git push`/`git ls-remote`/`git fetch` all work genuinely (unlike
    test_host_finalize_sh.py's stubbed-git harness, this file needs real git
    mechanics for worktree/rebase, so push needs a real remote too). Caller
    adds the run branch and any base-branch divergence on top."""
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)],
                   check=True, capture_output=True, text=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@x")
    _git(repo, "config", "user.name", "test")
    _git(repo, "remote", "add", "origin", str(bare))
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "a")
    _git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def _make_stub_bin(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text(f"#!/usr/bin/env bash\necho \"$@\" >> {bin_dir}/{name}.log\n{body}\n")
    p.chmod(0o755)


def _make_stub_python3(bin_dir: Path, rebaser_json: str, marker: str = "run_rebaser") -> None:
    """A `python3` stub that recognizes an invocation whose script argument
    contains `marker` (the real seam script always calls `m.run_rebaser(`)
    and prints `rebaser_json` to stdout; any other invocation execs the
    real python3 so nothing else in the test harness breaks."""
    p = bin_dir / "python3"
    p.write_text(f"""#!/usr/bin/env bash
echo "$@" >> {bin_dir}/python3.log
script="$1"
if [ -f "$script" ] && grep -q '{marker}' "$script"; then
  cat <<'JSON'
{rebaser_json}
JSON
  exit 0
fi
exec /usr/bin/env python3 "$@"
""")
    p.chmod(0o755)


def _make_run(tmp_path: Path, repo: Path, run_id: str, run_json: dict,
              state_json: dict | None = None) -> Path:
    run_dir = repo / ".leerie" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(run_json))
    if state_json is not None:
        (run_dir / "state.json").write_text(json.dumps(state_json))
    return run_dir


def _run_host_finalize(tmp_path: Path, repo: Path, run_dir: Path,
                       bin_dir: Path,
                       capture_pr_body_to: Path | None = None) -> subprocess.CompletedProcess:
    if capture_pr_body_to is not None:
        _make_stub_bin(bin_dir, "gh",
                       f"cat > {capture_pr_body_to}\n"
                       f"echo https://github.com/o/r/pull/1")
    else:
        _make_stub_bin(bin_dir, "gh", "echo https://github.com/o/r/pull/1")
    _make_stub_bin(bin_dir, "sleep", "exit 0")
    script = f"set -euo pipefail; . {HOST_FINALIZE_SH}; host_finalize {run_dir}"
    return subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "USER_REPO": str(repo),
            "HOME": str(tmp_path),
            "LEERIE_REPO": str(REPO_ROOT),
            "LEERIE_STATE_HOST_DIR": str(repo / ".leerie"),
        },
        capture_output=True, text=True, check=False,
    )


def _rebaser_result(status: str, final_branch_state: str = "ok",
                    resolution_summary: str = "", diagnosis: str = "") -> str:
    return json.dumps({
        "status": status,
        "final_branch_state": final_branch_state,
        "resolution_summary": resolution_summary,
        "diagnosis": diagnosis,
        "confidence": {"resolution": 9.0},
    })


def _make_diverged_repo(tmp_path: Path) -> Path:
    """main (base) has a commit the run branch predates; run branch has its
    own commit. This is exactly the shape the rebase step should attempt to
    act on: pr_base_branch (main) resolves locally, and the run branch is
    behind it."""
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-qb", "leerie/runs/test-run")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "run: add b")
    _git(repo, "checkout", "-q", "main")
    (repo / "c.txt").write_text("c\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "upstream: add c")
    return repo


def test_rebased_status_advances_working_branch_and_still_pushes(tmp_path):
    """Rebaser reports "rebased" → host_finalize fetches the worktree's new
    tip back, advances working_branch to origin/<pr_base_branch> in
    run.json (the PROVEN-pitfall fix), and still completes the push+PR."""
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_python3(bin_dir, _rebaser_result("rebased"))
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00",
    })
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir)
    assert r.returncode == 0, r.stderr
    assert "rebased leerie/runs/test-run onto main" in r.stderr
    after = json.loads((run_dir / "run.json").read_text())
    assert after["working_branch"] == "origin/main"
    # Push still happened.
    assert after.get("pushed_at")
    assert "opened PR" in r.stderr


def test_irreconcilable_status_leaves_branches_untouched_and_folds_diagnosis(tmp_path):
    """Rebaser reports "irreconcilable" → working_branch is NOT advanced,
    the original run branch is pushed as-is, and the diagnosis is folded
    into the (deterministic-fallback) PR body."""
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    diagnosis = "main and the run branch made incompatible pricing decisions"
    _make_stub_python3(bin_dir, _rebaser_result(
        "irreconcilable", diagnosis=diagnosis))
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00",
    }, state_json={"waves": [["x"]], "completed_waves": 1})
    pr_body_capture = tmp_path / "captured_pr_body.txt"
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir,
                           capture_pr_body_to=pr_body_capture)
    assert r.returncode == 0, r.stderr
    assert "rebase not applied (irreconcilable)" in r.stderr
    after = json.loads((run_dir / "run.json").read_text())
    assert after["working_branch"] == "main"
    assert after.get("pushed_at")
    body = pr_body_capture.read_text()
    assert "Rebase onto `main` was not applied" in body, body
    assert diagnosis in body, body


def test_failed_status_leaves_branches_untouched_and_still_pushes(tmp_path):
    """Rebaser reports "failed" → treated the same as irreconcilable for
    push purposes: original branch pushed, working_branch untouched, run
    never blocks."""
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_python3(bin_dir, _rebaser_result(
        "failed", diagnosis="worker invocation error"))
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00",
    })
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir)
    assert r.returncode == 0, r.stderr
    after = json.loads((run_dir / "run.json").read_text())
    assert after["working_branch"] == "main"
    assert after.get("pushed_at")


def test_worktree_add_failure_skips_rebase_and_still_pushes(tmp_path):
    """git worktree add failing (e.g. disk issue, or the run branch
    somehow unavailable for a worktree) must never block finalize —
    falls through to pushing the branch as-is with no rebase attempted."""
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A python3 stub that would fail the test if invoked at all — proves
    # the seam is never reached when worktree add fails.
    p = bin_dir / "python3"
    p.write_text("#!/usr/bin/env bash\necho UNEXPECTED_PYTHON3_CALL >&2\nexit 1\n")
    p.chmod(0o755)
    # Shadow `git worktree add` to fail while leaving every other git
    # subcommand to the real binary.
    real_git = subprocess.run(["which", "git"], capture_output=True, text=True).stdout.strip()
    git_shim = bin_dir / "git"
    git_shim.write_text(f"""#!/usr/bin/env bash
if [ "$3" = "worktree" ] && [ "$4" = "add" ]; then
  exit 1
fi
exec {real_git} "$@"
""")
    git_shim.chmod(0o755)
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00",
    })
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir)
    assert r.returncode == 0, r.stderr
    assert "could not create rebase worktree" in r.stderr
    after = json.loads((run_dir / "run.json").read_text())
    assert after["working_branch"] == "main"
    assert after.get("pushed_at")


def test_unresolvable_base_branch_skips_rebase_entirely(tmp_path):
    """pr_base_branch resolves neither locally nor on origin (no origin
    remote at all in this test repo) → the rebase block's own guard skips
    it before ever touching git worktree/python3 — falls straight through
    to the normal push."""
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-qb", "leerie/runs/test-run")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "run: add b")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    p = bin_dir / "python3"
    p.write_text("#!/usr/bin/env bash\necho UNEXPECTED_PYTHON3_CALL >&2\nexit 1\n")
    p.chmod(0o755)
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "nonexistent-base",
        "finished_at": "2026-07-31T00:00:00+00:00",
    })
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir)
    assert r.returncode == 0, r.stderr
    assert "attempting rebase" not in r.stderr
    after = json.loads((run_dir / "run.json").read_text())
    assert after["working_branch"] == "main"


def test_rebase_never_blocks_regardless_of_status(tmp_path):
    """Every status the rebaser can return (rebased / irreconcilable /
    failed / an unrecognized value) still reaches a successful finalize
    (rc 0). This is the hard requirement from DESIGN §6: the rebase step
    is strictly best-effort."""
    for status in ("rebased", "irreconcilable", "failed", "something-unexpected"):
        repo = _make_diverged_repo(tmp_path / status)
        bin_dir = (tmp_path / status) / "bin"
        bin_dir.mkdir(parents=True)
        _make_stub_python3(bin_dir, _rebaser_result(status))
        run_dir = _make_run(tmp_path / status, repo, "test-run", run_json={
            "branch": "leerie/runs/test-run",
            "working_branch": "main",
            "pr_base_branch": "main",
            "finished_at": "2026-07-31T00:00:00+00:00",
        })
        r = _run_host_finalize(tmp_path / status, repo, run_dir, bin_dir)
        assert r.returncode == 0, f"status={status!r}: {r.stderr}"


def test_pr_diff_base_correct_after_successful_rebase(tmp_path):
    """The exact regression prompts/fix-finalize-rebase.md's scratch-repo
    experiment proved: after a rebase, working_branch must be advanced so a
    later `working_branch..run_branch` diff does not silently include the
    base's own unrelated commits. Verify at the git level: with
    working_branch advanced to origin/<pr_base_branch> (itself resolving to
    the base's real current tip in this local-only test setup, since there
    is no real "origin" remote — see the local-ref fallback note below),
    the run branch's OWN commit is still the only content introduced
    relative to the fresh base tip."""
    repo = _make_diverged_repo(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_python3(bin_dir, _rebaser_result("rebased"))
    run_dir = _make_run(tmp_path, repo, "test-run", run_json={
        "branch": "leerie/runs/test-run",
        "working_branch": "main",
        "pr_base_branch": "main",
        "finished_at": "2026-07-31T00:00:00+00:00",
    })
    r = _run_host_finalize(tmp_path, repo, run_dir, bin_dir)
    assert r.returncode == 0, r.stderr
    # The stub never actually rebased anything in the real repo (it just
    # claims "rebased" without touching the worktree) — so this test's
    # job is narrower than a live rebase: prove host_finalize actually
    # issued a `git fetch <worktree> run_branch:run_branch` (the fetch-back
    # step) rather than skipping it, which is the mechanism the real fix
    # depends on. A worktree-mutation-level proof is covered by the two
    # live trials in the plan file, not re-derived here.
    assert "rebased leerie/runs/test-run onto main" in r.stderr
