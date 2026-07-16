"""Tests for `rescue_integrator_work` (DESIGN §12 *salvage if there is
something to salvage*).

Run 879defae's wave-2 integrator resolved six conflict hunks correctly, then
crashed on PID exhaustion. leerie ran `git merge --abort` and died, discarding
the resolution and killing a 13.5-hour run. Verified against a real git repo:
`merge --abort` reverts a resolved-but-uncommitted file to its pre-merge
content, leaving no stash and no reachable object — the work is simply gone.

The load-bearing detail these tests pin: the rescue must NOT depend on the
integrator having committed. `integrator-feat-006` never ran `git commit` (only
`integrator-feat-005` did), so a rescue gated on `check_merge_committed` would
decline exactly when it matters. It must also survive an *unmerged index*,
which is what defeats both `git stash push` and `git stash create`
("Cannot save the current index state").
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest


def _run(coro):
    return asyncio.run(coro)


def _git(*args, cwd, **kw):
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True, **kw)


@pytest.fixture()
def conflicted_repo(tmp_path):
    """A repo left mid-merge with a conflict, mirroring the state an
    integrator inherits."""
    repo = tmp_path / "staging"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "f.txt").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)

    _git("checkout", "-qb", "side", cwd=repo)
    (repo / "f.txt").write_text("side\n")
    _git("commit", "-qam", "side", cwd=repo)

    base = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo).stdout.strip()
    _git("checkout", "-q", "-", cwd=repo)
    (repo / "f.txt").write_text("main\n")
    _git("commit", "-qam", "main", cwd=repo)
    _git("merge", "side", cwd=repo)  # conflicts by construction
    assert (repo / ".git" / "MERGE_HEAD").exists(), "fixture must be mid-merge"
    del base
    return repo


def test_rescues_uncommitted_resolution(leerie, conflicted_repo):
    """The feat-006 case: resolution in the working tree, no merge commit.
    The content must survive `git merge --abort`."""
    (conflicted_repo / "f.txt").write_text("RESOLVED-BY-INTEGRATOR\n")

    ref = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))
    assert ref, "a resolved-but-uncommitted tree must be rescuable"

    _git("merge", "--abort", cwd=conflicted_repo)
    assert (conflicted_repo / "f.txt").read_text() == "main\n", (
        "sanity: merge --abort must have destroyed the working-tree resolution")

    shown = _git("show", f"{ref}:f.txt", cwd=conflicted_repo)
    assert shown.returncode == 0, f"rescued ref unreadable: {shown.stderr}"
    assert shown.stdout == "RESOLVED-BY-INTEGRATOR\n", (
        "the rescued ref must hold the integrator's resolution, not the "
        "pre-merge content")


def test_rescue_does_not_require_a_merge_commit(leerie, conflicted_repo):
    """Explicit regression pin: `check_merge_committed` returns an error here
    (MERGE_HEAD exists), and the rescue must still succeed. Gating the rescue
    on that predicate would decline the exact case worth saving."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")

    err = _run(leerie.check_merge_committed(conflicted_repo))
    assert err, "fixture sanity: this tree IS mid-merge"

    ref = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))
    assert ref, "rescue must not be gated on the merge having been committed"


def test_rescue_captures_untracked_files(leerie, conflicted_repo):
    """An integrator may create new files while resolving; they are part of
    the resolution and must be captured."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")
    (conflicted_repo / "new.txt").write_text("added-by-integrator\n")

    ref = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))
    assert ref

    shown = _git("show", f"{ref}:new.txt", cwd=conflicted_repo)
    assert shown.returncode == 0
    assert shown.stdout == "added-by-integrator\n"


def test_rescue_leaves_real_index_and_worktree_untouched(leerie,
                                                         conflicted_repo):
    """The rescue must be observation-only: it runs on the crash path, where
    corrupting the tree would turn a bad situation into an unrecoverable one."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")
    before = _git("status", "--porcelain", cwd=conflicted_repo).stdout

    _run(leerie.rescue_integrator_work(conflicted_repo, "feat-006", "run1"))

    after = _git("status", "--porcelain", cwd=conflicted_repo).stdout
    assert before == after, "rescue must not mutate the index or working tree"
    assert (conflicted_repo / ".git" / "MERGE_HEAD").exists(), (
        "rescue must not conclude or abort the merge itself")
    assert (conflicted_repo / "f.txt").read_text() == "RESOLVED\n"


def test_rescue_cleans_up_its_temp_index(leerie, conflicted_repo):
    """The throwaway index must not be left behind in .git/."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")
    _run(leerie.rescue_integrator_work(conflicted_repo, "feat-006", "run1"))
    leftovers = list((conflicted_repo / ".git").glob("leerie-rescue-index-*"))
    assert leftovers == [], f"temp index left behind: {leftovers}"


def test_rescue_returns_none_when_nothing_to_save(leerie, tmp_path):
    """A clean tree matching HEAD has no resolution to rescue — the caller
    uses None to say so honestly rather than naming an empty ref."""
    repo = tmp_path / "clean"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "f.txt").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)

    ref = _run(leerie.rescue_integrator_work(repo, "feat-006", "run1"))
    assert ref is None


def test_rescue_returns_none_outside_a_git_repo(leerie, tmp_path):
    """A non-repo degrades to None via the returncode branch (git exits 128
    rather than raising), not via the exception handler."""
    not_a_repo = tmp_path / "nope"
    not_a_repo.mkdir()
    ref = _run(leerie.rescue_integrator_work(not_a_repo, "feat-006", "run1"))
    assert ref is None


def test_rescue_swallows_an_exception(leerie, conflicted_repo, monkeypatch):
    """The bare `except` is a deliberate crash-path guarantee: this function
    runs *because* a worker already died, so a rescue failure must degrade to
    None rather than raise a second error over the first.

    Pinned separately from the non-repo case above, which never reaches the
    handler: `git read-tree` on a non-repo returns rc 128 instead of raising,
    so that test exits via the returncode branch. The realistic raising
    failure is `git` missing from PATH, which surfaces as FileNotFoundError
    out of asyncio's create_subprocess_exec."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")

    async def boom(*a, **kw):
        raise FileNotFoundError("git: command not found")

    monkeypatch.setattr(leerie, "run_proc", boom)
    ref = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))
    assert ref is None, "a raising git must degrade to None, not propagate"


def test_rescue_cleans_up_temp_index_even_when_it_raises(leerie,
                                                         conflicted_repo,
                                                         monkeypatch):
    """The `finally` must hold on the exception path too — otherwise a crashed
    rescue leaves a stale index in .git/ for the next attempt to trip over."""
    (conflicted_repo / "f.txt").write_text("RESOLVED\n")
    real = leerie.run_proc
    calls = []

    async def boom_after_seed(cmd, **kw):
        calls.append(cmd)
        if len(calls) == 1:          # let read-tree create the index file
            return await real(cmd, **kw)
        raise FileNotFoundError("git vanished mid-rescue")

    monkeypatch.setattr(leerie, "run_proc", boom_after_seed)
    ref = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))
    assert ref is None
    leftovers = list((conflicted_repo / ".git").glob("leerie-rescue-index-*"))
    assert leftovers == [], f"temp index survived an exception: {leftovers}"


def test_rescue_ref_is_namespaced_by_run_and_subtask(leerie, conflicted_repo):
    """Two crashed integrators in one run must not clobber each other."""
    (conflicted_repo / "f.txt").write_text("A\n")
    ref_a = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-005", "run1"))
    (conflicted_repo / "f.txt").write_text("B\n")
    ref_b = _run(leerie.rescue_integrator_work(
        conflicted_repo, "feat-006", "run1"))

    assert ref_a != ref_b
    assert _git("show", f"{ref_a}:f.txt", cwd=conflicted_repo).stdout == "A\n"
    assert _git("show", f"{ref_b}:f.txt", cwd=conflicted_repo).stdout == "B\n"
