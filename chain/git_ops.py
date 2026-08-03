"""chain.git_ops — laptop-side git/gh operations for chain wave transitions.

Imported by the leerie launcher's ``chain`` wave-sequencer (laptop
side) to synth-merge each wave's branches into a staged base before
launching the next wave. Wave-N branches are pushed to origin by each
per-job ``host_finalize`` invocation (single-run flow); this module
fetches them locally and produces the stage branch for wave N+1.

Functions:
  - synth_merge_branches: build a stage branch by merging several
    completed dep branches into a fresh base — used to seed the next
    wave with its predecessors' work already in tree.

Workers never invoke this module. All GitHub credential touches happen
on the laptop using its existing gh auth + ~/.git-credentials.
"""
from __future__ import annotations

from pathlib import Path
import subprocess

from chain._log import die as _die


def _run(args: list[str], cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=check, capture_output=True, text=True)


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


# ---------------------------------------------------------------------------
# Synth-merge: combine multiple dep branches into a stage branch
# ---------------------------------------------------------------------------


class SynthMergeConflict(Exception):
    """A merge during synth_merge_branches conflicted.

    Carries the failing branch name and the git output for diagnostics
    so the laptop's chain wave loop can surface it to the user and
    pause the chain (DESIGN §19).
    """

    def __init__(self, branch: str, output: str) -> None:
        super().__init__(f"merge conflict on branch {branch!r}: {output.strip()}")
        self.branch = branch
        self.output = output


def synth_merge_branches(
    repo_path: str | Path,
    base_branch: str,
    dep_branches: list[str],
    stage_branch_name: str,
) -> str:
    """Build a stage branch by merging each of *dep_branches* into *base_branch*.

    Steps performed on *repo_path* (which must be a clone of the target repo):

    1. ``git fetch origin`` — refresh remotes for every dep branch.
    2. ``git checkout -B <stage_branch_name> origin/<base_branch>`` — fresh
       branch off the current base. Force-recreates the branch so retries
       (after a failed first attempt) start clean.
    3. For each *dep_branch* in order:
         ``git merge --no-ff origin/<dep_branch>``
       On non-zero exit, raises :class:`SynthMergeConflict` carrying the
       branch name + git output. The repo is left in a conflicted state
       so a caller (or a human) can inspect.

    Files-disjoint by design (per the migration plan); merges should
    fast-forward or auto-merge cleanly. Conflicts indicate a planner
    defect or an unexpected file collision.

    Returns the stage branch name on success.
    """
    repo_path = Path(repo_path)

    fetch = _run(["git", "fetch", "origin"], cwd=repo_path, check=False)
    if fetch.returncode != 0:
        _die(f"synth_merge_branches: git fetch failed: {fetch.stderr.strip()}")

    checkout = _run(
        ["git", "checkout", "-B", stage_branch_name, f"origin/{base_branch}"],
        cwd=repo_path, check=False,
    )
    if checkout.returncode != 0:
        _die(
            f"synth_merge_branches: failed to create {stage_branch_name!r} from "
            f"origin/{base_branch}: {checkout.stderr.strip()}"
        )

    for branch in dep_branches:
        # `git merge --no-ff` always creates a merge commit, which needs
        # an author/committer identity. The repo may not have local
        # `user.email`/`user.name` set, and the laptop's global git
        # config might also be unset (rare but possible — also the
        # case in CI containers). Pass a bot identity defensively via
        # `-c` so the merge succeeds regardless of ambient config.
        merge = _run(
            [
                "git",
                "-c", "user.email=leerie-chain@bot.invalid",
                "-c", "user.name=leerie-chain",
                "merge", "--no-ff", "--no-edit", f"origin/{branch}",
            ],
            cwd=repo_path, check=False,
        )
        if merge.returncode != 0:
            # Leave the repo in its conflicted state for inspection.
            raise SynthMergeConflict(
                branch=branch,
                output=merge.stdout + merge.stderr,
            )

    return stage_branch_name
