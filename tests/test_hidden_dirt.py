"""Review round 16: dirt a worker hides from `git status` (H1), and the base
an implementer's work must sit on (M2) — DESIGN §8 *A worker leaves its
tree clean*, *One restore*.

H1: `git update-index --skip-worktree/--assume-unchanged` hides a tracked
edit from status; a pattern in the shared `info/exclude` (or
`core.excludesFile`) hides an untracked file. The checker reads the index
flags, and the restore clears them. Round 17 H2 made the ignore sources
leerie's (DESIGN §8 *The settings are leerie's*): an `info/exclude` or
`core.excludesFile` a worker writes is a settings line, and the restore
puts the settings back, after which `clean` removes the file they hid.

M2: the implementer's base is where its subtask branch forked from the run
branch, so a continuation that amends its own earlier attempt is clean.

The round's drivers (review-r16-proofs/exec), rewritten to assert the fix.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest


def _git(cwd, *a, check=True):
    return subprocess.run(["git", *a], cwd=cwd, check=check,
                          capture_output=True, text=True).stdout.strip()


BR = "leerie/runs/x"


class _St:
    """The fields `_ensure_run_repo` reads."""

    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_id = "x"
        self.run_dir = run_dir
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


def _staging(tmp_path: Path, leerie) -> tuple[Path, Path, str]:
    """Staging on `BR`, a worktree of the run's own repository (DESIGN §6
    *The run's own repository*), as leerie makes it. Returns the run
    repository, staging and its HEAD."""
    u = tmp_path / "user"
    u.mkdir()
    _git(u, "init", "-q", "-b", "main")
    _git(u, "config", "user.email", "t@x")
    _git(u, "config", "user.name", "t")
    (u / ".gitignore").write_text("node_modules/\n")
    (u / "app.py").write_text("LIMIT = 1\n")
    _git(u, "add", "-A")
    _git(u, "commit", "-qm", "base")
    run_dir = tmp_path / "runs" / "x"
    run_dir.mkdir(parents=True)
    r = leerie._ensure_run_repo(_St(u, run_dir))
    _git(r, "branch", BR, "main")
    stg = run_dir / "worktrees" / "staging"
    _git(r, "worktree", "add", "-q", str(stg), BR)
    return r, stg, _git(stg, "rev-parse", "HEAD")


def _changed(repo: Path, name: str) -> str:
    return (f"git settings changed: {repo / name} differs from leerie's "
            f"copy {repo.parent / 'repo-settings' / name}")


def _dirt(leerie, stg, base):
    return asyncio.run(leerie._tree_dirt(stg, branch=BR, base=base))


def _restore(leerie, stg, base):
    return asyncio.run(leerie._restore_tree(stg, base, where="t", branch=BR))


def test_a_skip_worktree_edit_is_dirt_and_the_restore_removes_it(
        leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "app.py").write_text("LIMIT = 2\n")
    _git(stg, "update-index", "--skip-worktree", "app.py")
    assert _git(stg, "status", "--porcelain") == ""     # status is blind
    # The fresh read (round 17 H2) sees the edit itself, the flag line why
    # status did not.
    assert _dirt(leerie, stg, base) == [
        " M app.py",
        "app.py is marked skip-worktree (an edit git status does not show)"]
    assert _restore(leerie, stg, base) is True
    assert (stg / "app.py").read_text() == "LIMIT = 1\n"
    assert _git(stg, "ls-files", "-v") .split() == ["H", ".gitignore", "H",
                                                    "app.py"]
    assert _dirt(leerie, stg, base) == []


def test_an_assume_unchanged_edit_is_dirt(leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "app.py").write_text("LIMIT = 2\n")
    _git(stg, "update-index", "--assume-unchanged", "app.py")
    assert _dirt(leerie, stg, base) == [
        " M app.py",
        "app.py is marked assume-unchanged (an edit git status does not "
        "show)"]
    assert _restore(leerie, stg, base) is True
    assert _dirt(leerie, stg, base) == []


def test_a_file_hidden_by_the_shared_info_exclude_is_dirt_and_removed(
        leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "conftest.py").write_text("import app\napp.LIMIT = 2\n")
    ex = Path(_git(stg, "rev-parse", "--path-format=absolute",
                   "--git-path", "info/exclude"))
    ex.parent.mkdir(parents=True, exist_ok=True)
    ex.write_text("conftest.py\n")
    assert ex.is_relative_to(r)                  # the run's shared file
    assert _git(stg, "status", "--porcelain") == ""
    assert _dirt(leerie, stg, base) == [_changed(r, "info")]
    assert _restore(leerie, stg, base) is True
    assert not (stg / "conftest.py").exists()
    assert _dirt(leerie, stg, base) == []


def test_a_file_hidden_by_core_excludes_file_is_dirt(leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    gx = tmp_path / "global-ignore"
    gx.write_text("helper.py\n")
    _git(stg, "config", "core.excludesFile", str(gx))
    (stg / "helper.py").write_text("H = 1\n")
    assert _git(stg, "status", "--porcelain") == ""
    # The worker's own tree wrote the run repository's config, and the
    # checker reads leerie's ignore file, not the one the key names.
    assert _dirt(leerie, stg, base) == [_changed(r, "config"),
                                        "?? helper.py"]


def test_a_tools_self_ignoring_cache_is_not_dirt(leerie, tmp_path):
    """A `.gitignore` of `*` inside a directory a tool writes is how pytest,
    mypy and ruff mark their caches; reading it as dirt made every Python
    run's staging die at its first measurement. Honoured (DESIGN §8 *A
    worker leaves its tree clean*, which names the residual)."""
    r, stg, base = _staging(tmp_path, leerie)
    (stg / ".pytest_cache" / "v").mkdir(parents=True)
    (stg / ".pytest_cache" / ".gitignore").write_text(
        "# Created by pytest automatically.\n*\n")
    (stg / ".pytest_cache" / "v" / "lastfailed").write_text("{}\n")
    assert _dirt(leerie, stg, base) == []


def test_what_the_committed_rules_ignore_is_never_dirt(leerie, tmp_path):
    """Exact in the other direction: installed dependencies, a package's
    own `.gitignore` inside them included, never trigger it."""
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "node_modules" / "pkg").mkdir(parents=True)
    (stg / "node_modules" / "pkg" / ".gitignore").write_text("*\n")
    (stg / "node_modules" / "pkg" / "index.js").write_text("x\n")
    assert _dirt(leerie, stg, base) == []
    assert _restore(leerie, stg, base) is True
    assert (stg / "node_modules" / "pkg" / "index.js").exists()


# --- M2 -----------------------------------------------------------------------

def test_an_amend_of_its_own_earlier_attempt_is_clean(leerie, tmp_path):
    """The driver: attempt 1 committed on the subtask branch, the
    continuation completed the fix with `git commit --amend`. With the base
    taken from the previous attempt's tip, the base line fired and correct
    work settled `unconverged`; the base is now where the subtask branch
    forked from the run branch."""
    # The run's own repository, `<run-dir>/repo` (DESIGN §6 *The run's own
    # repository*), where the run's branches are.
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    (r / "a.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    run_tip = _git(r, "rev-parse", "HEAD")
    _git(r, "branch", "leerie/runs/x")
    sub = "leerie/subtasks/x/bugfix-001"

    class St:
        run_id = "x"
        repo_root = r
        run_dir = tmp_path
    # No subtask branch yet: the run branch's tip.
    assert asyncio.run(leerie._fork_point(St, "bugfix-001")) == run_tip
    _git(r, "branch", sub, "leerie/runs/x")
    wt = tmp_path / "wt"
    _git(r, "worktree", "add", "-q", str(wt), sub)
    (wt / "a.py").write_text("x = 2\n")
    _git(wt, "commit", "-qam", "fix (attempt 1)")
    # The run branch moves on (a later wave): the fork point does not.
    (r / "b.py").write_text("y\n")
    _git(r, "add", "b.py")
    _git(r, "commit", "-qm", "other wave")
    _git(r, "branch", "-f", "leerie/runs/x", "HEAD")
    base = asyncio.run(leerie._fork_point(St, "bugfix-001"))
    assert base == run_tip
    (wt / "a.py").write_text("x = 3\n")
    _git(wt, "commit", "-q", "--amend", "-am", "fix")
    asked: list[str] = []

    async def reask(note):
        asked.append(note)
    assert asyncio.run(leerie._clean_tree_gate(
        wt, caps=dict(leerie.DEFAULT_CAPS), label="bugfix-001", reask=reask,
        branch=sub, base=base)) == []
    assert asked == []


def test_a_branch_reset_behind_its_fork_point_is_named_with_its_act(
        leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "app.py").write_text("LIMIT = 2\n")
    _git(stg, "commit", "-qam", "work")
    start = _git(stg, "rev-parse", "HEAD")
    _git(stg, "reset", "-q", "--hard", "HEAD~1")
    notes: list[str] = []

    async def reask(note):
        notes.append(note)
        _git(stg, "reset", "-q", "--hard", start)
    assert asyncio.run(leerie._clean_tree_gate(
        stg, caps=dict(leerie.DEFAULT_CAPS), label="t", reask=reask,
        branch=BR, base=start)) == []
    assert f"git rebase {start[:12]}" in notes[0]
    assert f"git checkout {BR}" not in notes[0]


# --- the shell half of the one restore: the same rule ------------------------

_LIB = Path(__file__).resolve().parents[1] / "scripts" / "worktree-lib.sh"


def _sh(fn: str, stg: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'source "{_LIB}"; {fn} "$@"', "sh", str(stg), *args],
        capture_output=True, text=True)


def _hide(shape: str, r: Path, stg: Path, tmp_path: Path) -> list[str]:
    """Make one hidden-dirt shape in `stg`; return what the checker says."""
    if shape in ("skip-worktree", "assume-unchanged"):
        (stg / "app.py").write_text("LIMIT = 2\n")
        _git(stg, "update-index", f"--{shape}", "app.py")
        return [" M app.py", f"app.py is marked {shape} (an edit git status "
                "does not show)"]
    if shape == "info-exclude":
        (stg / "conftest.py").write_text("import app\napp.LIMIT = 2\n")
        ex = Path(_git(stg, "rev-parse", "--path-format=absolute",
                       "--git-path", "info/exclude"))
        ex.parent.mkdir(parents=True, exist_ok=True)
        ex.write_text("conftest.py\n")
        return [_changed(r, "info")]
    gx = tmp_path / "global-ignore"
    gx.write_text("helper.py\n")
    _git(stg, "config", "core.excludesFile", str(gx))
    (stg / "helper.py").write_text("H = 1\n")
    return [_changed(r, "config"), "?? helper.py"]


SHAPES = ["skip-worktree", "assume-unchanged", "info-exclude",
          "core-excludesfile"]


@pytest.mark.parametrize("shape", SHAPES)
def test_the_shell_checker_reads_what_the_python_one_reads(leerie, tmp_path,
                                                           shape):
    r, stg, base = _staging(tmp_path, leerie)
    want = _hide(shape, r, stg, tmp_path)
    assert _git(stg, "status", "--porcelain") == ""      # status is blind
    sh = _sh("leerie_tree_dirt", stg)
    assert sh.returncode == 0, sh.stderr
    assert sh.stdout.splitlines() == want
    assert asyncio.run(leerie._tree_dirt(stg)) == want


@pytest.mark.parametrize("shape", SHAPES)
def test_the_shell_reset_restores_each_hidden_shape(leerie, tmp_path,
                                                    shape):
    """The round's shell driver (`leerie_reset_worktree` exited 0 with the
    skip-worktree edit in place), for every shape: the real function
    clears it, and its postcondition and Python's checker agree."""
    r, stg, base = _staging(tmp_path, leerie)
    _hide(shape, r, stg, tmp_path)
    p = _sh("leerie_reset_worktree", stg, BR, base)
    assert p.returncode == 0, p.stderr
    assert (stg / "app.py").read_text() == "LIMIT = 1\n"
    assert not (stg / "conftest.py").exists()
    assert not (stg / "helper.py").exists()
    assert _sh("leerie_tree_dirt", stg).stdout == ""
    assert asyncio.run(leerie._tree_dirt(stg, branch=BR, base=base)) == []


def test_the_shell_reset_keeps_what_the_tree_ignores(leerie, tmp_path):
    r, stg, base = _staging(tmp_path, leerie)
    (stg / "node_modules" / "pkg").mkdir(parents=True)
    (stg / "node_modules" / "pkg" / "index.js").write_text("x\n")
    (stg / ".pytest_cache").mkdir()
    (stg / ".pytest_cache" / ".gitignore").write_text("*\n")
    p = _sh("leerie_reset_worktree", stg, BR, base)
    assert p.returncode == 0, p.stderr
    assert (stg / "node_modules" / "pkg" / "index.js").exists()
    assert (stg / ".pytest_cache" / ".gitignore").exists()
