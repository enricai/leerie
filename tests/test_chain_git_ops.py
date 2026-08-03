"""Tests for chain.git_ops against a local temp git repo with gh stubbed."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

import chain.git_ops as git_ops


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def origin_repo(tmp_path: Path) -> Path:
    """Bare git repo that acts as the remote 'origin'."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    # `-b main` pins the bare repo's HEAD symbolic ref to refs/heads/main
    # regardless of the runner's init.defaultBranch config. Without this,
    # CI runners (which default to `master`) leave HEAD pointing at a
    # nonexistent branch after we push HEAD:main — subsequent `git clone`
    # then checks out an empty working tree.
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True, capture_output=True,
    )
    return origin


@pytest.fixture()
def seeded_origin(origin_repo: Path, tmp_path: Path) -> Path:
    """Bare origin with an initial commit on main so branches can be created."""
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(seed)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", str(origin_repo)],
        cwd=seed, check=True, capture_output=True,
    )
    (seed / "README.md").write_text("hello\n")
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=T",
         "commit", "--allow-empty", "-m", "init", "--"],
        cwd=seed, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )
    subprocess.run(
        ["git", "push", "-u", "origin", "HEAD:main"],
        cwd=seed, check=True, capture_output=True,
    )
    return origin_repo


# ---------------------------------------------------------------------------
# create_stage_branch
# ---------------------------------------------------------------------------


@pytest.fixture()
def local_clone(seeded_origin: Path, tmp_path: Path) -> Path:
    """A non-bare clone of seeded_origin on main."""
    clone_dir = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(seeded_origin), str(clone_dir)],
        check=True, capture_output=True,
    )
    # Ensure the local clone knows origin's main as the base.
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=clone_dir, check=True, capture_output=True,
    )
    return clone_dir


def test_create_stage_branch_creates_new_branch(local_clone: Path) -> None:
    branch = git_ops.create_stage_branch(local_clone, "abc123")
    assert branch == "stage-abc123"
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=local_clone, check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "stage-abc123"


def test_create_stage_branch_idempotent(local_clone: Path) -> None:
    """Calling create_stage_branch twice does not crash."""
    git_ops.create_stage_branch(local_clone, "abc123")
    # Add a commit so HEAD is not on main, then call again — must not crash.
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=local_clone, check=True, capture_output=True,
    )
    branch = git_ops.create_stage_branch(local_clone, "abc123")
    assert branch == "stage-abc123"
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=local_clone, check=True, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "stage-abc123"


def test_create_stage_branch_name_includes_chain_id(local_clone: Path) -> None:
    branch = git_ops.create_stage_branch(local_clone, "xyz-789")
    assert branch == "stage-xyz-789"


def test_create_stage_branch_off_base(local_clone: Path) -> None:
    """Stage branch HEAD matches base branch HEAD."""
    base_sha = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=local_clone, check=True, capture_output=True, text=True,
    ).stdout.strip()
    git_ops.create_stage_branch(local_clone, "newchain", base_branch="main")
    stage_sha = subprocess.run(
        ["git", "rev-parse", "stage-newchain"],
        cwd=local_clone, check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert stage_sha == base_sha


# ---------------------------------------------------------------------------
# synth_merge_branches — combine dep branches into stage
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_origin_with_dep_branches(
    seeded_origin: Path, tmp_path: Path
) -> tuple[Path, list[str]]:
    """Seeded origin + two disjoint feature branches pushed to it.

    Returns (origin_path, [branch_names]).
    """
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(seeded_origin), str(work)],
        check=True, capture_output=True,
    )
    branches = []
    for i, fname in enumerate(["alpha.txt", "beta.txt"]):
        branch = f"feat-{i}"
        subprocess.run(["git", "checkout", "main"], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=work, check=True, capture_output=True,
        )
        (work / fname).write_text(f"content {i}\n")
        subprocess.run(["git", "add", fname], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T",
             "commit", "-m", f"add {fname}"],
            cwd=work, check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
                 "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=work, check=True, capture_output=True,
        )
        branches.append(branch)
    return seeded_origin, branches


def test_synth_merge_branches_combines_disjoint_branches(
    seeded_origin_with_dep_branches: tuple[Path, list[str]], tmp_path: Path
) -> None:
    origin, branches = seeded_origin_with_dep_branches
    clone = tmp_path / "merge-clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)

    stage = git_ops.synth_merge_branches(
        repo_path=clone,
        base_branch="main",
        dep_branches=branches,
        stage_branch_name="stage-merge-test",
    )
    assert stage == "stage-merge-test"

    # Both feature files should now be in tree.
    assert (clone / "alpha.txt").read_text() == "content 0\n"
    assert (clone / "beta.txt").read_text() == "content 1\n"

    # And HEAD is on the stage branch.
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=clone, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert head == "stage-merge-test"


def test_synth_merge_branches_raises_on_conflict(
    seeded_origin: Path, tmp_path: Path
) -> None:
    """Two branches that both modify the same file → SynthMergeConflict."""
    work = tmp_path / "conflict-work"
    subprocess.run(["git", "clone", str(seeded_origin), str(work)], check=True, capture_output=True)

    # Create two branches that both write to README.md → guaranteed conflict.
    for i, content in enumerate(["A side\n", "B side\n"]):
        branch = f"conflict-{i}"
        subprocess.run(["git", "checkout", "main"], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=work, check=True, capture_output=True,
        )
        (work / "README.md").write_text(content)
        subprocess.run(["git", "add", "README.md"], cwd=work, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t.com", "-c", "user.name=T",
             "commit", "-m", f"conflict-{i}"],
            cwd=work, check=True, capture_output=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
                 "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=work, check=True, capture_output=True,
        )

    clone = tmp_path / "merge-clone"
    subprocess.run(["git", "clone", str(seeded_origin), str(clone)], check=True, capture_output=True)

    with pytest.raises(git_ops.SynthMergeConflict) as exc_info:
        git_ops.synth_merge_branches(
            repo_path=clone,
            base_branch="main",
            dep_branches=["conflict-0", "conflict-1"],
            stage_branch_name="stage-conflict",
        )
    # Conflict is reported on the second branch (the first merge succeeds).
    assert exc_info.value.branch == "conflict-1"


def test_synth_merge_branches_force_recreates_stage(
    seeded_origin_with_dep_branches: tuple[Path, list[str]], tmp_path: Path
) -> None:
    """Re-running synth_merge with the same stage name starts fresh (no stale commits)."""
    origin, branches = seeded_origin_with_dep_branches
    clone = tmp_path / "force-clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)

    # First run.
    git_ops.synth_merge_branches(
        repo_path=clone,
        base_branch="main",
        dep_branches=[branches[0]],
        stage_branch_name="stage-retry",
    )
    # Add a junk commit on stage branch.
    (clone / "junk.txt").write_text("junk\n")
    subprocess.run(["git", "add", "junk.txt"], cwd=clone, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=T",
         "commit", "-m", "junk"],
        cwd=clone, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )

    # Re-run from main — junk commit should be gone, stage branch reset.
    git_ops.synth_merge_branches(
        repo_path=clone,
        base_branch="main",
        dep_branches=branches,
        stage_branch_name="stage-retry",
    )
    assert not (clone / "junk.txt").exists()
    assert (clone / "alpha.txt").exists()
    assert (clone / "beta.txt").exists()
