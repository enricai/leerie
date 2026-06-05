"""chain.git_ops — git and gh PR operations for the leerie-chain app.

The chain app runs inside a container that holds the GH_DISPATCH_PAT.
Functions here clone the target repo, manage the stage-<chain-id>
branch used for Wave B sequencing, and push branches / open PRs via gh.

This is the chain app's counterpart to the host-side scripts/host-finalize.sh.
The actor is different (chain app, not user's shell) but the git/gh mechanic
is the same — clone via HTTPS PAT URL, push, gh pr create.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _die(msg: str, code: int = 1) -> None:
    print(f"leerie-chain: error: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def _run(args: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


def _pat_url(repo_url: str, pat: str) -> str:
    """Embed PAT into an HTTPS GitHub URL for credential-free clone/push."""
    if repo_url.startswith("https://"):
        return repo_url.replace("https://", f"https://{pat}@", 1)
    raise ValueError(f"clone_target requires an https:// URL; got: {repo_url!r}")


def clone_target(repo_url: str, pat: str, clone_dir: str | Path) -> Path:
    """Clone *repo_url* into *clone_dir* using *pat* as the HTTPS credential.

    Returns the path to the local clone root.
    *clone_dir* must not exist yet — the caller is responsible for choosing
    a fresh temp directory.
    """
    clone_dir = Path(clone_dir)
    url_with_pat = _pat_url(repo_url, pat)
    result = _run(["git", "clone", url_with_pat, str(clone_dir)], check=False)
    if result.returncode != 0:
        _die(f"git clone failed for {repo_url}: {result.stderr.strip()}")
    return clone_dir


def create_stage_branch(repo_path: str | Path, chain_id: str, base_branch: str = "main") -> str:
    """Create (or check out) the stage-<chain_id> branch off *base_branch*.

    Idempotent: if the branch already exists locally, this checks it out
    rather than raising an error.  Returns the branch name.
    """
    repo_path = Path(repo_path)
    branch = f"stage-{chain_id}"

    # Check whether the branch already exists locally.
    result = _run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=repo_path,
        check=False,
    )
    if result.returncode == 0:
        # Branch exists — just check it out.
        _run(["git", "checkout", branch], cwd=repo_path)
    else:
        # Branch does not exist — create it off base_branch.
        _run(["git", "checkout", base_branch], cwd=repo_path)
        _run(["git", "checkout", "-b", branch], cwd=repo_path)

    return branch


def push_branch(repo_path: str | Path, branch_name: str) -> None:
    """Push *branch_name* to origin with tracking set up.

    Shells out to ``git push -u origin <branch>``.  Raises SystemExit on
    failure (matching the die() pattern used elsewhere in this package).
    """
    repo_path = Path(repo_path)
    result = _run(
        ["git", "push", "-u", "origin", branch_name],
        cwd=repo_path,
        check=False,
    )
    if result.returncode != 0:
        _die(
            f"git push -u origin {branch_name} failed: {result.stderr.strip()}"
        )


def open_pr(
    repo_path: str | Path,
    head: str,
    base: str,
    title: str,
    body: str,
) -> str:
    """Open a GitHub PR using ``gh pr create``.

    Shells out to gh with ``--base``, ``--head``, ``--title``, and
    ``--body-file -`` (body piped via stdin) — matching the arg shape in
    host-finalize.sh:182-186.

    Returns the PR URL on success.  Raises SystemExit on failure.
    """
    repo_path = Path(repo_path)
    result = subprocess.run(
        [
            "gh", "pr", "create",
            "--base", base,
            "--head", head,
            "--title", title,
            "--body-file", "-",
        ],
        input=body,
        cwd=repo_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _die(
            f"gh pr create failed (head={head!r} base={base!r}): "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    # gh pr create prints the PR URL as the last line of stdout.
    pr_url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    return pr_url
