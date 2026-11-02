"""Commit a test checkout's files, for the launcher's Dockerfile generator.

The generator reads the committed tree at HEAD, never the checkout's paths
(DESIGN §6 *Leerie never reads through a link a repository holds*; §6 *The
orchestrator's root is leerie's; a repository's root is only a confined
process's*: the image's build context is that tree). A harness that writes
a lockfile into a checkout commits it here before the generator runs.
`.leerie/` stays out of the commit: it is the operator's, untracked, unless
a test commits it itself (the committed-Dockerfile authority tests)."""
from __future__ import annotations

import subprocess
from pathlib import Path

_ID = ["-c", "user.email=t@t", "-c", "user.name=t",
       "-c", "commit.gpgsign=false"]


def commit_tree(repo: Path | str) -> None:
    """`git init` `repo` when it is not a repository, then commit every file
    but `.leerie/` (nothing when nothing changed)."""
    repo = Path(repo)
    if not repo.is_dir():
        return
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-q", str(repo)], check=True,
                       capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A", "--", ".",
                    ":(exclude).leerie"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), *_ID, "commit", "-q",
                    "--allow-empty", "-m", "test tree"], check=True,
                   capture_output=True)
