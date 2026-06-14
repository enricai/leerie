"""chain.git_ops — laptop-side git/gh operations for chain wave transitions.

Imported by the leerie launcher's ``--chain`` wave-sequencer (laptop
side) to synth-merge each wave's branches into a staged base before
launching the next wave. Wave-N branches are pushed to origin by each
per-job ``host_finalize`` invocation (single-run flow); this module
fetches them locally and produces the stage branch for wave N+1.

Functions:
  - synth_merge_branches: build a stage branch by merging several
    completed dep branches into a fresh base — used to seed the next
    wave with its predecessors' work already in tree.
  - clone_target / finalize_run / write_audit_artifact / open_pr: kept
    for compatibility with the existing test suite and any future
    automated paths; the laptop-side sequencer uses only
    synth_merge_branches in the MVP.

Workers never invoke this module. All GitHub credential touches happen
on the laptop using its existing gh auth + ~/.git-credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

from chain._log import die as _die


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


# ---------------------------------------------------------------------------
# Synth-merge: combine multiple dep branches into a stage branch
# ---------------------------------------------------------------------------


class SynthMergeConflict(Exception):
    """A merge during synth_merge_branches conflicted.

    Carries the failing branch name and the git output for diagnostics
    so the laptop's --chain wave loop can surface it to the user and
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
        # Bot identity matches write_audit_artifact's convention.
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


def fetch_branch(repo_path: str | Path, branch: str) -> None:
    """``git fetch origin <branch>:<branch>`` into the local repo.

    Kept for the existing test suite; not on the v5 Shape A active
    chain code path (the laptop wave loop fetches branches via
    ``synth_merge_branches``'s internal ``git fetch origin`` call).
    """
    repo_path = Path(repo_path)
    result = _run(
        ["git", "fetch", "origin", f"{branch}:{branch}"],
        cwd=repo_path, check=False,
    )
    if result.returncode != 0:
        _die(
            f"fetch_branch: git fetch origin {branch} failed: "
            f"{result.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Finalize one run: push its branch + open PR
# ---------------------------------------------------------------------------


def finalize_run(
    repo_path: str | Path,
    head_branch: str,
    base_branch: str,
    pr_title: str,
    pr_body: str,
) -> str:
    """Push *head_branch* to origin and open a PR against *base_branch*.

    Kept for the existing test suite; not on the v5 Shape A active
    chain code path. Chain runs use ``scripts/host-finalize.sh`` per
    job (same as single-run ``--runtime fly``); the wave loop only
    calls ``synth_merge_branches`` from this module.

    Returns the PR URL on success. Raises SystemExit on push or
    PR-create failure.
    """
    push_branch(repo_path, head_branch)
    return open_pr(
        repo_path=repo_path,
        head=head_branch,
        base=base_branch,
        title=pr_title,
        body=pr_body,
    )


# ---------------------------------------------------------------------------
# Audit artifact
# ---------------------------------------------------------------------------


_AUDIT_DIR = "_leerie-chains"


def write_audit_artifact(
    chain_snapshot: dict,
    repo_url: str | None = None,
    pat: str | None = None,
    base_branch: str = "main",
) -> None:
    """Push `_leerie-chains/<chain-id>/chain.json` to the target repo.

    Kept for the existing test suite; not on the v5 Shape A active
    chain code path. The single-run flow each wave job invokes
    already records run history under ``$LEERIE_STATE_HOST_DIR/runs/``,
    so a separate audit artifact in the target repo is no longer
    needed for the laptop-side wave loop.

    Args:
        chain_snapshot: dict containing at minimum ``id`` and
                        ``target`` (the target repo URL).
        repo_url: Override of the target repo URL. Defaults to
                  ``chain_snapshot["target"]``.
        pat: GitHub PAT for the push. Defaults to ``GH_DISPATCH_PAT`` env.
        base_branch: Branch to commit the artifact to. Default ``main``.

    Operationally:
      1. Clone the target into a tempdir.
      2. Check out *base_branch*.
      3. Write the artifact at ``_leerie-chains/<chain-id>/chain.json``.
      4. Commit and ``git push origin <base_branch>``.
    """
    target = repo_url or chain_snapshot.get("target")
    if not target:
        raise ValueError("write_audit_artifact: no target repo URL")
    pat = pat or os.environ.get("GH_DISPATCH_PAT", "")
    if not pat:
        raise ValueError(
            "write_audit_artifact: PAT missing (set GH_DISPATCH_PAT or pass `pat=`)"
        )

    chain_id = chain_snapshot["id"]

    # Use a tempdir as the clone — keep the audit-artifact work
    # ephemeral.
    with tempfile.TemporaryDirectory(prefix=f"leerie-audit-{chain_id}-") as tmpdir:
        clone_root = Path(tmpdir) / "clone"
        clone_target(target, pat, clone_root)
        _run(["git", "checkout", base_branch], cwd=clone_root)

        artifact_dir = clone_root / _AUDIT_DIR / chain_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "chain.json"
        artifact_path.write_text(
            json.dumps(chain_snapshot, indent=2, default=str) + "\n"
        )

        # Configure identity for the commit (bot identity matching
        # how the artifact would be authored under any future
        # automated path).
        _run(["git", "config", "user.email", "leerie-chain@bot.invalid"], cwd=clone_root)
        _run(["git", "config", "user.name", "leerie-chain"], cwd=clone_root)
        _run(["git", "add", str(artifact_path.relative_to(clone_root))], cwd=clone_root)

        # If nothing changed (re-run of self-destruct), `git commit` would
        # exit non-zero; treat that as success.
        commit = _run(
            ["git", "commit", "-m", f"leerie-chain: audit for {chain_id}"],
            cwd=clone_root, check=False,
        )
        if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
            _die(f"write_audit_artifact: commit failed: {commit.stderr.strip()}")

        push = _run(
            ["git", "push", "origin", base_branch],
            cwd=clone_root, check=False,
        )
        if push.returncode != 0:
            _die(f"write_audit_artifact: push failed: {push.stderr.strip()}")
