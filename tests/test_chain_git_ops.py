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
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    return origin


@pytest.fixture()
def seeded_origin(origin_repo: Path, tmp_path: Path) -> Path:
    """Bare origin with an initial commit on main so branches can be created."""
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", str(seed)], check=True, capture_output=True)
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
