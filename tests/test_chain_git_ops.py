"""Tests for chain.git_ops against a local temp git repo with gh stubbed."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import textwrap

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


@pytest.fixture()
def gh_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a fake 'gh' script to a tmp bin dir and prepend it to PATH."""
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    gh_script = bin_dir / "gh"
    gh_script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            # Record invocation args so tests can inspect them.
            echo "$@" >> "$GH_STUB_CALL_LOG"
            echo "https://github.com/owner/repo/pull/1"
            exit 0
            """
        )
    )
    gh_script.chmod(gh_script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    call_log = tmp_path / "gh-calls.log"
    call_log.write_text("")
    monkeypatch.setenv("GH_STUB_CALL_LOG", str(call_log))
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    return call_log


# ---------------------------------------------------------------------------
# clone_target
# ---------------------------------------------------------------------------


def test_clone_target_local_file_url(seeded_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """clone_target works when given a file:// URL (no network needed)."""
    clone_dir = tmp_path / "clone"
    # Patch _pat_url to accept file:// so we can exercise the clone logic
    # without GitHub network access.
    monkeypatch.setattr(
        git_ops,
        "_pat_url",
        lambda repo_url, pat: f"file://{seeded_origin}",
    )
    result = git_ops.clone_target(f"https://github.com/x/y", "mytoken", clone_dir)
    assert result == clone_dir
    assert (clone_dir / ".git").is_dir()


def test_clone_target_pat_embedded_in_url() -> None:
    """_pat_url embeds the PAT into an https:// URL."""
    url = git_ops._pat_url("https://github.com/owner/repo", "MY_PAT")
    assert url == "https://MY_PAT@github.com/owner/repo"


def test_clone_target_rejects_non_https_url() -> None:
    with pytest.raises(ValueError, match="https://"):
        git_ops._pat_url("git@github.com:owner/repo.git", "pat")


def test_clone_target_failure_calls_die(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """clone_target calls _die (sys.exit) when git clone fails."""
    monkeypatch.setattr(
        git_ops,
        "_pat_url",
        lambda repo_url, pat: "file:///nonexistent-repo-that-does-not-exist",
    )
    with pytest.raises(SystemExit) as exc_info:
        git_ops.clone_target("https://github.com/x/y", "tok", tmp_path / "no-clone")
    assert exc_info.value.code == 1


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
# push_branch
# ---------------------------------------------------------------------------


def test_push_branch_pushes_to_origin(local_clone: Path, seeded_origin: Path) -> None:
    git_ops.create_stage_branch(local_clone, "push-test")
    git_ops.push_branch(local_clone, "stage-push-test")
    # Verify branch exists in origin bare repo.
    result = subprocess.run(
        ["git", "branch"],
        cwd=seeded_origin, check=True, capture_output=True, text=True,
    )
    assert "stage-push-test" in result.stdout


def test_push_branch_failure_calls_die(local_clone: Path, tmp_path: Path) -> None:
    """push_branch calls die when git push fails (no such remote branch)."""
    # Point origin to a non-existent remote so push fails.
    subprocess.run(
        ["git", "remote", "set-url", "origin", "file:///nonexistent-bare"],
        cwd=local_clone, check=True, capture_output=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        git_ops.push_branch(local_clone, "main")
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# open_pr
# ---------------------------------------------------------------------------


def test_open_pr_calls_gh_with_expected_args(
    local_clone: Path, gh_stub: Path
) -> None:
    url = git_ops.open_pr(
        local_clone,
        head="leerie/runs/abc/feat-001",
        base="main",
        title="leerie: my task",
        body="## Summary\n\nhello",
    )
    assert url == "https://github.com/owner/repo/pull/1"

    calls = gh_stub.read_text()
    # gh stub records all args space-joined on one line per call.
    assert "pr create" in calls
    assert "--base main" in calls
    assert "--head leerie/runs/abc/feat-001" in calls
    assert "--title" in calls
    assert "--body-file -" in calls


def test_open_pr_returns_pr_url(local_clone: Path, gh_stub: Path) -> None:
    url = git_ops.open_pr(local_clone, "head-branch", "base-branch", "t", "b")
    assert url.startswith("https://")


def test_open_pr_failure_calls_die(
    local_clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """open_pr calls die when gh exits nonzero."""
    bin_dir = tmp_path / "fail-bin"
    bin_dir.mkdir()
    failing_gh = bin_dir / "gh"
    failing_gh.write_text("#!/bin/sh\necho 'gh error' >&2\nexit 1\n")
    failing_gh.chmod(failing_gh.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    with pytest.raises(SystemExit) as exc_info:
        git_ops.open_pr(local_clone, "h", "b", "t", "body")
    assert exc_info.value.code == 1


def test_open_pr_body_piped_via_stdin(
    local_clone: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """open_pr pipes the body string to gh's stdin (--body-file -)."""
    bin_dir = tmp_path / "stdin-bin"
    bin_dir.mkdir()
    stdin_log = tmp_path / "gh-stdin.log"
    gh_script = bin_dir / "gh"
    gh_script.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            # Capture whatever arrives on stdin.
            cat >> {stdin_log}
            echo "https://github.com/owner/repo/pull/99"
            exit 0
            """
        )
    )
    gh_script.chmod(gh_script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    body = "## Summary\n\nThis body must arrive via stdin."
    git_ops.open_pr(local_clone, "my-head", "main", "my title", body)

    received = stdin_log.read_text()
    assert received == body


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


# ---------------------------------------------------------------------------
# fetch_branch
# ---------------------------------------------------------------------------


def test_fetch_branch_pulls_remote_branch(
    seeded_origin_with_dep_branches: tuple[Path, list[str]], tmp_path: Path
) -> None:
    origin, branches = seeded_origin_with_dep_branches
    clone = tmp_path / "fetch-clone"
    subprocess.run(["git", "clone", str(origin), str(clone)], check=True, capture_output=True)
    # Before fetch, the remote branch isn't a local ref.
    rev = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branches[0]}"],
        cwd=clone, capture_output=True,
    )
    assert rev.returncode != 0

    git_ops.fetch_branch(clone, branches[0])

    # After fetch, the branch is a local ref.
    rev2 = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branches[0]}"],
        cwd=clone, capture_output=True,
    )
    assert rev2.returncode == 0


def test_fetch_branch_failure_calls_die(
    local_clone: Path
) -> None:
    """Fetching a non-existent branch → SystemExit."""
    with pytest.raises(SystemExit):
        git_ops.fetch_branch(local_clone, "nonexistent-branch")


# ---------------------------------------------------------------------------
# finalize_run — push + open_pr in one call
# ---------------------------------------------------------------------------


def test_finalize_run_pushes_and_opens_pr(
    seeded_origin: Path, tmp_path: Path, gh_stub: Path
) -> None:
    work = tmp_path / "finalize-work"
    subprocess.run(["git", "clone", str(seeded_origin), str(work)], check=True, capture_output=True)
    # Make a feature branch with a commit.
    subprocess.run(
        ["git", "checkout", "-b", "leerie/runs/abc-001"],
        cwd=work, check=True, capture_output=True,
    )
    (work / "result.txt").write_text("done\n")
    subprocess.run(["git", "add", "result.txt"], cwd=work, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=T", "commit", "-m", "result"],
        cwd=work, check=True, capture_output=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
             "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
    )

    url = git_ops.finalize_run(
        repo_path=work,
        head_branch="leerie/runs/abc-001",
        base_branch="main",
        pr_title="leerie: run abc-001",
        pr_body="## Summary\n\nbody body",
    )
    assert url.startswith("https://")

    # Confirm the branch landed in the bare origin.
    out = subprocess.run(
        ["git", "branch"], cwd=seeded_origin, capture_output=True, text=True, check=True,
    ).stdout
    assert "leerie/runs/abc-001" in out

    # Confirm gh was called with the right args.
    calls = gh_stub.read_text()
    assert "--head leerie/runs/abc-001" in calls
    assert "--base main" in calls


# ---------------------------------------------------------------------------
# write_audit_artifact
# ---------------------------------------------------------------------------


def test_write_audit_artifact_commits_chain_json(
    seeded_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit artifact lands at _leerie-chains/<id>/chain.json on main."""
    # Bypass the PAT-URL rewrite so we can use a file:// origin.
    monkeypatch.setattr(
        git_ops, "_pat_url",
        lambda repo_url, pat: f"file://{seeded_origin}",
    )
    monkeypatch.setenv("GH_DISPATCH_PAT", "fake-pat")

    chain_snapshot = {
        "id": "abc-chain-123",
        "target": "https://github.com/x/y",
        "queue_json": '{"jobs": {}}',
        "wave_state": "done",
        "status": "done",
        "paused": None,
        "created_at": "2026-06-14T00:00:00+00:00",
        "updated_at": "2026-06-14T00:01:00+00:00",
        "completed_at": "2026-06-14T00:01:00+00:00",
        "runs": [
            {"id": "r0", "status": "done", "wave": "0", "branch": "leerie/runs/r0"},
        ],
    }
    git_ops.write_audit_artifact(chain_snapshot)

    # Now clone the origin somewhere else and verify the artifact is on main.
    verify = tmp_path / "verify"
    subprocess.run(
        ["git", "clone", f"file://{seeded_origin}", str(verify)],
        check=True, capture_output=True,
    )
    artifact = verify / "_leerie-chains" / "abc-chain-123" / "chain.json"
    assert artifact.exists()
    text = artifact.read_text()
    import json
    parsed = json.loads(text)
    assert parsed["id"] == "abc-chain-123"
    assert parsed["status"] == "done"
    assert parsed["runs"][0]["id"] == "r0"


def test_write_audit_artifact_missing_pat_raises() -> None:
    with pytest.raises(ValueError, match="PAT"):
        git_ops.write_audit_artifact(
            {"id": "x", "target": "https://x/y"},
            pat="",
        )
