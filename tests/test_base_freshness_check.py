"""`host_base_freshness_check` — refuse a fresh run on a stale base
(DESIGN §6 *A fresh run refuses a stale base*).

Motivation (measured, 2026-09-26): an operator merged a run's PR on the
forge and re-ran the same task one minute later, before the local pull.
The new run planned against the pre-merge tree
(`repo_state_before_planning.head` identical to its predecessor's),
re-solved the merged findings from scratch, and its finalize rebase
landed the older solution over the just-merged fix — a regression the
next run had to re-fix.

Pure git/bash tests against real repositories with a real `origin` (a
bare repo next door) and a real second writer advancing it — following
tests/test_prepush_preflight.py: the probe's whole value is running real
git, so stubbing it would test nothing. The single most important case
is the measured one: origin advanced by someone else, local tracking ref
STALE — only the function's own fetch can discover the staleness.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_repo_first

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"
LAUNCHER = REPO_ROOT / "leerie"


@pytest.fixture
def repos(tmp_path: Path) -> dict:
    """origin (bare) + work (the operator's checkout, upstream configured)
    + writer (a second clone that can advance origin behind work's back —
    the forge-side merge in the measured incident)."""
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(work)],
                   check=True)
    run_git_repo_first(work, "config", "user.email", "t@example.com")
    run_git_repo_first(work, "config", "user.name", "t")
    (work / "a.txt").write_text("x\n")
    run_git_repo_first(work, "add", "a.txt")
    run_git_repo_first(work, "commit", "-qm", "init")
    run_git_repo_first(work, "remote", "add", "origin", str(origin))
    assert run_git_repo_first(
        work, "push", "-q", "--no-verify", "-u", "origin",
        "main").returncode == 0
    writer = tmp_path / "writer"
    subprocess.run(["git", "clone", "-q", str(origin), str(writer)],
                   check=True)
    run_git_repo_first(writer, "config", "user.email", "w@example.com")
    run_git_repo_first(writer, "config", "user.name", "w")
    return {"origin": origin, "work": work, "writer": writer}


def _advance_origin(repos: dict, fname: str = "merged.txt") -> None:
    writer = repos["writer"]
    (writer / fname).write_text("merged work\n")
    run_git_repo_first(writer, "add", fname)
    run_git_repo_first(writer, "commit", "-qm", f"merge {fname}")
    assert run_git_repo_first(
        writer, "push", "-q", "--no-verify", "origin",
        "main").returncode == 0


def _check(repo: Path) -> subprocess.CompletedProcess:
    """Call the real function under the `set -euo pipefail` its caller
    sets."""
    return subprocess.run(
        ["bash", "-c",
         f"set -euo pipefail; . {HOST_FINALIZE_SH}; "
         f'host_base_freshness_check "$1"', "_", str(repo)],
        capture_output=True, text=True, check=False,
    )


# --- the contract ---------------------------------------------------------

def test_up_to_date_is_silent(repos):
    r = _check(repos["work"])
    assert r.returncode == 0
    assert r.stderr == ""


def test_the_measured_incident_shape_is_refused(repos):
    """Origin advanced by someone else; the LOCAL tracking ref is stale
    (no fetch ran). Only the function's own fetch can see the staleness —
    a comparison against the last-fetched ref alone would pass. This is
    the run-3 incident, mechanically."""
    _advance_origin(repos)
    work = repos["work"]
    # Precondition: the stale tracking ref equals HEAD, so a fetch-less
    # comparison could not refuse.
    head = run_git_repo_first(work, "rev-parse", "HEAD").stdout.strip()
    stale = run_git_repo_first(
        work, "rev-parse", "origin/main").stdout.strip()
    assert head == stale
    r = _check(work)
    assert r.returncode == 1
    assert "BEHIND" in r.stderr
    assert "1 commit(s)" in r.stderr
    assert "pull --ff-only" in r.stderr
    assert "LEERIE_SKIP_FRESHNESS_CHECK=1" in r.stderr


def test_ahead_passes(repos):
    work = repos["work"]
    (work / "local.txt").write_text("local\n")
    run_git_repo_first(work, "add", "local.txt")
    run_git_repo_first(work, "commit", "-qm", "local work")
    r = _check(work)
    assert r.returncode == 0


def test_diverged_passes(repos):
    """Local commits exist AND origin advanced: the operator is doing
    something deliberate; 'behind' is the signature, divergence is not."""
    work = repos["work"]
    (work / "local.txt").write_text("local\n")
    run_git_repo_first(work, "add", "local.txt")
    run_git_repo_first(work, "commit", "-qm", "local work")
    _advance_origin(repos)
    r = _check(work)
    assert r.returncode == 0


def test_no_origin_passes(tmp_path):
    solo = tmp_path / "solo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(solo)],
                   check=True)
    run_git_repo_first(solo, "config", "user.email", "t@example.com")
    run_git_repo_first(solo, "config", "user.name", "t")
    (solo / "a.txt").write_text("x\n")
    run_git_repo_first(solo, "add", "a.txt")
    run_git_repo_first(solo, "commit", "-qm", "init")
    r = _check(solo)
    assert r.returncode == 0
    assert r.stderr == ""


def test_detached_head_passes(repos):
    work = repos["work"]
    head = run_git_repo_first(work, "rev-parse", "HEAD").stdout.strip()
    run_git_repo_first(work, "checkout", "-q", head)
    r = _check(work)
    assert r.returncode == 0


def test_no_upstream_falls_back_to_origin_branch(repos):
    """A branch without upstream config but with a same-named origin
    branch still gets the check (origin/<branch> fallback)."""
    work = repos["work"]
    run_git_repo_first(work, "branch", "--unset-upstream")
    _advance_origin(repos)
    r = _check(work)
    assert r.returncode == 1
    assert "BEHIND" in r.stderr


def test_local_upstream_passes_silently(repos):
    """Review round 1: `git branch --set-upstream-to=<local>` sets
    branch.<name>.remote = "." and @{u} becomes a BARE branch name. The
    remote/branch split then treated a branch name as a remote — a
    failed fetch with a misleading offline warning, and an ancestry
    refusal against a LOCAL base, which is not the merge→re-run race
    signature. A local upstream is out of the guard's remit: silent
    pass, even when strictly behind it."""
    work = repos["work"]
    run_git_repo_first(work, "checkout", "-q", "-b", "feature")
    run_git_repo_first(work, "branch", "--set-upstream-to=main", "feature")
    # Put feature strictly BEHIND its local upstream.
    run_git_repo_first(work, "checkout", "-q", "main")
    (work / "ahead.txt").write_text("x\n")
    run_git_repo_first(work, "add", "ahead.txt")
    run_git_repo_first(work, "commit", "-qm", "main moves ahead")
    run_git_repo_first(work, "checkout", "-q", "feature")
    r = _check(work)
    assert r.returncode == 0
    assert "could not refresh" not in r.stderr
    assert "BEHIND" not in r.stderr


def test_unreachable_origin_warns_and_passes(repos):
    """Offline must never block run start: the fetch fails, a warning
    says the comparison used the last-fetched state, and an up-to-date
    (per that state) checkout proceeds."""
    work = repos["work"]
    run_git_repo_first(work, "remote", "set-url", "origin",
                       "/nonexistent/path/origin.git")
    r = _check(work)
    assert r.returncode == 0
    assert "could not refresh" in r.stderr


def test_stale_tracking_ref_refuses_even_offline(repos):
    """Fetched-but-not-pulled, then offline: the last-fetched state alone
    already proves staleness, and the failed refresh must not erase it."""
    work = repos["work"]
    _advance_origin(repos)
    assert run_git_repo_first(work, "fetch", "-q", "origin",
                              "main").returncode == 0
    run_git_repo_first(work, "remote", "set-url", "origin",
                       "/nonexistent/path/origin.git")
    r = _check(work)
    assert r.returncode == 1
    assert "BEHIND" in r.stderr


# --- launcher wiring -------------------------------------------------------

class TestLauncherWiring:
    def test_launcher_calls_the_check_as_step_5(self):
        text = LAUNCHER.read_text()
        assert 'host_base_freshness_check "$USER_REPO" || exit 1' in text

    def test_gated_on_fresh_runs_and_the_env_hatch(self):
        """Resume continues its recorded baseline; the env hatch is the
        documented deliberate-stale-base path. Both gates must sit on the
        same `if` as the call."""
        text = LAUNCHER.read_text()
        i_call = text.index('host_base_freshness_check "$USER_REPO"')
        block = text[i_call - 800:i_call]
        assert '"$IS_RESUME" = "false"' in block
        assert "LEERIE_SKIP_FRESHNESS_CHECK" in block

    def test_env_hatch_shares_the_prepush_vocabulary(self):
        """Truthy spellings must match the sibling hatch — two adjacent
        escape hatches with different boolean vocabularies is operator
        hostile."""
        text = LAUNCHER.read_text()
        i = text.index("LEERIE_SKIP_FRESHNESS_CHECK")
        block = text[i:i + 200]
        assert "1|true|TRUE|yes|YES" in block
