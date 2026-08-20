"""`scripts/planning-worktree.sh` — the disposable tree judgment workers run in
(DESIGN §12 *Judgment-worker isolation*, L2).

Real git repositories throughout, and the script itself rather than a
reproduction of it: a hand-copied block is body-blind by construction, which is
the trap `tests/test_no_duplicate_launcher_blocks.py` exists to prevent.

The two behaviours that are easy to get wrong and expensive to get wrong:

  * **reset, not reuse.** The satisfied-probe is handed no diff — its prompt
    tells it to judge "the CURRENT working tree / HEAD", so its cwd is the only
    thing determining what it sees. A tree an earlier judgment worker wrote
    into would make it answer "already satisfied" and silently drop real work.
  * **`clean -fd`, never `-fdx`.** Gitignored content (node_modules, .venv)
    must survive or every resume re-pays the install.
"""
from __future__ import annotations

import subprocess

import pytest

from tests.conftest import run_git_repo_first

SCRIPT = "scripts/planning-worktree.sh"


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    run_git_repo_first(repo, "init", "-q", ".")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    (repo / "src.txt").write_text("original\n")
    (repo / ".gitignore").write_text("node_modules/\n")
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "dep.txt").write_text("dep\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-qm", "init")
    return repo


def _run(repo, state_dir, run_id="r1"):
    import pathlib
    script = pathlib.Path(__file__).resolve().parents[1] / SCRIPT
    return subprocess.run(
        ["bash", str(script), run_id], cwd=str(repo), capture_output=True,
        text=True, env={"PATH": "/usr/bin:/bin", "HOME": str(repo),
                        "LEERIE_STATE_DIR": str(state_dir)})


@pytest.fixture()
def env(tmp_path):
    repo = _repo(tmp_path)
    state = tmp_path / "state"
    wt = state / "runs" / "r1" / "worktrees" / "planning"
    return repo, state, wt


class TestCreate:
    def test_creates_a_detached_worktree_at_head(self, env):
        repo, state, wt = env
        r = _run(repo, state)
        assert r.returncode == 0, r.stderr
        assert wt.is_dir()
        # detached: symbolic-ref fails on a detached HEAD
        assert subprocess.run(["git", "-C", str(wt), "symbolic-ref", "-q", "HEAD"],
                      capture_output=True, text=True, check=False).returncode != 0
        assert (run_git_repo_first(wt, "rev-parse", "HEAD").stdout.strip()
                == run_git_repo_first(repo, "rev-parse", "HEAD").stdout.strip())

    def test_creates_no_branch(self, env):
        """The reapers (cleanup.sh, _cleanup_on_abnormal_exit, `leerie prune`)
        know only `leerie/runs/<id>` and `leerie/subtasks/<id>/`. A fourth
        namespace would leak forever — the stale-branch problem `prune` was
        written to fix. This test is what fails if someone "improves" the
        script to use `-b`."""
        repo, state, _ = env
        before = run_git_repo_first(repo, "for-each-ref", "--format=%(refname)",
                      "refs/heads").stdout
        _run(repo, state)
        after = run_git_repo_first(repo, "for-each-ref", "--format=%(refname)",
                     "refs/heads").stdout
        assert before == after, f"refs changed: {before!r} -> {after!r}"

    def test_prints_the_absolute_path(self, env):
        repo, state, wt = env
        r = _run(repo, state)
        line = r.stdout.strip().splitlines()[-1]
        assert line.startswith("planning-worktree: /")
        assert line.split("planning-worktree:", 1)[1].strip() == str(
            wt.resolve())


class TestResetOnReentry:
    def test_worker_changes_are_discarded(self, env):
        repo, state, wt = env
        _run(repo, state)
        (wt / "src.txt").write_text("TAMPERED\n")
        (wt / "invented.txt").write_text("junk\n")
        _run(repo, state)
        assert (wt / "src.txt").read_text() == "original\n"
        assert not (wt / "invented.txt").exists()

    def test_a_worker_commit_is_discarded(self, env):
        repo, state, wt = env
        _run(repo, state)
        (wt / "src.txt").write_text("TAMPERED\n")
        run_git_repo_first(wt, "add", "-A")
        run_git_repo_first(wt, "-c", "user.email=x@x", "-c", "user.name=x",
             "commit", "-qm", "worker")
        _run(repo, state)
        assert (run_git_repo_first(wt, "rev-parse", "HEAD").stdout.strip()
                == run_git_repo_first(repo, "rev-parse", "HEAD").stdout.strip())

    def test_head_drift_is_corrected(self, env):
        """The resume hazard: the real checkout's HEAD moves across a pause
        (a sibling run merging its PR). A worktree pinned at the old sha would
        plan against a tree that no longer exists."""
        repo, state, wt = env
        _run(repo, state)
        (repo / "src.txt").write_text("newer\n")
        run_git_repo_first(repo, "add", "-A")
        run_git_repo_first(repo, "commit", "-qm", "second")
        _run(repo, state)
        assert (wt / "src.txt").read_text() == "newer\n"
        assert (run_git_repo_first(wt, "rev-parse", "HEAD").stdout.strip()
                == run_git_repo_first(repo, "rev-parse", "HEAD").stdout.strip())

    def test_gitignored_content_survives_the_reset(self, env):
        """`clean -fd`, not `-fdx`. Without this every resume re-pays a full
        install; the comment in the script says so, and this is what makes
        that comment enforceable."""
        repo, state, wt = env
        _run(repo, state)
        (wt / "node_modules").mkdir(exist_ok=True)
        (wt / "node_modules" / "dep.txt").write_text("installed\n")
        _run(repo, state)
        assert (wt / "node_modules" / "dep.txt").read_text() == "installed\n"

    def test_untracked_and_ignored_are_treated_differently(self, env):
        """Anti-vacuity partner for the two tests above: they would both pass
        against a script that cleans nothing at all."""
        repo, state, wt = env
        _run(repo, state)
        (wt / "plain_untracked.txt").write_text("x\n")
        (wt / "node_modules").mkdir(exist_ok=True)
        (wt / "node_modules" / "keep.txt").write_text("y\n")
        _run(repo, state)
        assert not (wt / "plain_untracked.txt").exists()
        assert (wt / "node_modules" / "keep.txt").exists()


class TestNoRealWorktreeInThisRepo:
    """`_ensure_planning_worktree` shells out to a real `git worktree add`
    rooted at `resolve_leerie_root()`, which with `LEERIE_STATE_DIR` unset is
    `<repo>/.leerie`. Every test driving the real `_run_phases` therefore
    created a full checkout of this repo inside this repo.

    It was invisible three ways over: `.leerie/*` is gitignored so
    `git status` stayed clean, the directories outlived the session, and the
    damage surfaced in `test_helper_naming_convention` — whose `tests/`
    exclusion is a relative-path prefix that a nested
    `.leerie/.../worktrees/planning/tests/...` copy does not match. Measured:
    two worktrees, 25 MB, one red test with no visible link to its cause."""

    def test_conftest_stubs_it_by_default(self):
        import pathlib as _p
        src = (_p.Path(__file__).resolve().parents[0]
               / "conftest.py").read_text()
        assert "_no_real_planning_worktree" in src
        i = src.index("def _no_real_planning_worktree")
        assert "autouse=True" in src[max(0, i - 200):i]
        assert "_ensure_planning_worktree" in src[i:i + 2000], (
            "the fixture no longer stubs the function it exists to stub")

    def test_driving_it_creates_no_worktree_in_this_repo(self, leerie,
                                                         tmp_path):
        """Behavioural partner: the source check above passes against a
        fixture that stubs the wrong thing."""
        before = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=False).stdout

        class _St:
            run_dir = tmp_path / "run"
            run_id = "guard"
            data: dict = {}

            def save(self):
                pass

        import asyncio
        asyncio.run(leerie._ensure_planning_worktree(_St()))
        after = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, check=False).stdout
        assert before == after, (
            "a real git worktree was registered against this repo; the "
            "conftest guard is not in effect")


class TestRepair:
    def test_orphaned_directory_is_removed_and_recreated(self, env):
        """A SIGKILLed container leaves the directory without its admin
        entry; `worktree add` then refuses with "already exists" on every
        subsequent resume, and neither `prune` nor `--force` covers it."""
        repo, state, wt = env
        _run(repo, state)
        run_git_repo_first(repo, "worktree", "remove", "--force", str(wt))
        wt.mkdir(parents=True)
        (wt / "stale.txt").write_text("left behind\n")
        r = _run(repo, state)
        assert r.returncode == 0, r.stderr
        assert (wt / "src.txt").exists()
        assert not (wt / "stale.txt").exists()

    def test_fails_outside_a_git_repository(self, tmp_path):
        import pathlib
        script = pathlib.Path(__file__).resolve().parents[1] / SCRIPT
        d = tmp_path / "nope"
        d.mkdir()
        r = subprocess.run(["bash", str(script), "r1"], cwd=str(d),
                           capture_output=True, text=True,
                           env={"PATH": "/usr/bin:/bin", "HOME": str(d),
                                "LEERIE_STATE_DIR": str(tmp_path / "s")})
        assert r.returncode != 0
        assert "not inside a git repository" in r.stderr
