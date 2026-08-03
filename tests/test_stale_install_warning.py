"""The launcher's stale-install warning (IMPLEMENTATION.md §0
*Stale-install warning*).

Running `leerie` never advances `$LEERIE_REPO`; only re-running install.sh
does. Nothing surfaced that, so an install could sit arbitrarily far behind
origin while the operator believed they were on current code — the measured
cost being two multi-hour funeralworks runs on 2026-08-02 that died at the
phase-3 wiring gate on a v0.9.100 install, reproducing the exact failure the
v0.9.101 auto-repair fixes.

`_warn_if_leerie_stale` is extracted verbatim from the launcher (the
`_extract_forwarding_loop` convention in `test_launcher_env_forwarding.py`)
and driven against real local git repositories — a fixture "origin" plus a
clone deliberately rewound behind it. No network.

The load-bearing case is `test_warns_when_the_cached_ref_is_stale`: without
the throttled fetch the guard reads a remote-tracking ref that is exactly as
stale as the checkout, and stays silent through precisely the failure it
exists to catch.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_guard() -> str:
    """Pull `_warn_if_leerie_stale` verbatim from the launcher so this test
    exercises the real function, not a copy of it."""
    src = LAUNCHER.read_text()
    m = re.search(
        r"(_warn_if_leerie_stale\(\) \{.*?\n\}\n)", src, re.DOTALL)
    assert m, "could not locate _warn_if_leerie_stale in the launcher"
    return m.group(1)


_HARNESS = r"""#!/usr/bin/env bash
set -euo pipefail

remote_log() { printf '[leerie] %s\n' "$*" >&2; }

LEERIE_REPO="__REPO__"
LEERIE_STATE_HOST_DIR="__STATE__"
LEERIE_VERSION="__VERSION__"

__GUARD__

_warn_if_leerie_stale || true
"""


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def _write_version(path: Path, version: str) -> None:
    manifest = path / ".claude-plugin"
    manifest.mkdir(parents=True, exist_ok=True)
    (manifest / "plugin.json").write_text(
        json.dumps({"name": "leerie", "version": version}, indent=2) + "\n")


@pytest.fixture()
def install(tmp_path):
    """An 'origin' at v0.9.102 and an install clone rewound to v0.9.100."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    _git(origin, "config", "user.email", "t@example.com")
    _git(origin, "config", "user.name", "t")
    _write_version(origin, "0.9.100")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "v0.9.100")

    clone = tmp_path / "install"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)],
                   check=True, capture_output=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "t")

    # origin moves ahead; the clone's cached remote ref stays behind.
    _write_version(origin, "0.9.102")
    _git(origin, "add", "-A")
    _git(origin, "commit", "-qm", "v0.9.102")

    state = tmp_path / "state"
    state.mkdir()
    return {"origin": origin, "clone": clone, "state": state}


def _run(install, version="0.9.100", repo=None):
    script = install["state"].parent / "harness.sh"
    script.write_text(
        _HARNESS
        .replace("__GUARD__", _extract_guard())
        .replace("__REPO__", str(repo or install["clone"]))
        .replace("__STATE__", str(install["state"]))
        .replace("__VERSION__", version)
    )
    return subprocess.run(["bash", str(script)], capture_output=True,
                          text=True, timeout=120)


# --------------------------------------------------------------------- #

def test_warns_when_the_cached_ref_is_stale(install):
    """THE load-bearing case. The clone has never fetched, so its cached
    remote-tracking ref points at the same commit as HEAD — a guard without
    the throttled fetch reports 'behind 0' and says nothing."""
    r = _run(install)
    assert "behind" in r.stderr, (
        "guard stayed silent on a stale install — the throttled fetch is "
        "missing or not reached; a cached-ref-only check cannot see this")
    assert "0.9.100" in r.stderr and "0.9.102" in r.stderr, (
        "both version strings must appear — a commit count alone is not "
        "actionable")
    assert "pull --ff-only" in r.stderr


def test_reports_the_commit_count(install):
    assert "1 commit(s) behind" in _run(install).stderr


def test_names_the_installed_path_not_the_dev_checkout(install):
    """The whole confusion this fixes: work happens in one checkout, runs
    execute another."""
    assert str(install["clone"]) in _run(install).stderr


def test_silent_when_up_to_date(install):
    _git(install["clone"], "fetch", "-q", "origin")
    _git(install["clone"], "merge", "-q", "--ff-only", "origin/main")
    r = _run(install, version="0.9.102")
    assert "behind" not in r.stderr
    assert r.returncode == 0


def test_never_blocks_the_run(install):
    """Warn-only: a stale install must still launch."""
    assert _run(install).returncode == 0


# --- the skip guards -------------------------------------------------- #

def test_silent_on_a_detached_head(install):
    sha = subprocess.run(
        ["git", "-C", str(install["clone"]), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    _git(install["clone"], "checkout", "-q", sha)
    r = _run(install)
    assert "behind" not in r.stderr and r.returncode == 0


def test_silent_without_an_upstream(install):
    """A branch with no upstream makes @{upstream} meaningless."""
    _git(install["clone"], "checkout", "-q", "-b", "local-only")
    r = _run(install)
    assert "behind" not in r.stderr and r.returncode == 0


def test_silent_when_not_a_git_checkout(install, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    r = _run(install, repo=plain)
    assert "behind" not in r.stderr and r.returncode == 0


def test_silent_when_the_remote_is_unreachable(install):
    """An offline host must never fail or spam a run."""
    _git(install["clone"], "remote", "set-url", "origin",
         "/nonexistent/path/to/repo.git")
    r = _run(install)
    assert r.returncode == 0
    assert "behind" not in r.stderr


# --- the throttle ------------------------------------------------------ #

def test_fetch_is_throttled_by_a_stamp_file(install):
    """A fresh stamp suppresses the fetch, so the guard falls back to the
    cached ref and stays quiet — which is what makes the throttle safe to
    run on every invocation."""
    stamp = install["state"] / ".fetch-stamp"
    stamp.touch()
    r = _run(install)
    assert "behind" not in r.stderr, (
        "a fresh stamp must suppress the fetch; this run should have used "
        "the (stale) cached ref and found nothing")


def test_stamp_is_created_after_a_fetch(install):
    stamp = install["state"] / ".fetch-stamp"
    assert not stamp.exists()
    _run(install)
    assert stamp.exists(), "without the stamp the fetch would run every time"


def test_stale_stamp_allows_the_fetch(install):
    """Older than a day → fetch again."""
    stamp = install["state"] / ".fetch-stamp"
    stamp.touch()
    old = time.time() - 2 * 86400
    os.utime(stamp, (old, old))
    assert "behind" in _run(install).stderr


# --- coupling guard ---------------------------------------------------- #

def test_guard_is_invoked_by_the_launcher():
    """The function is inert unless something calls it — and the `|| true` is
    REQUIRED, not cosmetic.

    The launcher runs under `set -euo pipefail`. `_warn_if_leerie_stale`
    assigns from a pipeline (`git show ... | awk ...`); if `git show` cannot
    find `plugin.json` on the upstream tree, pipefail makes the assignment
    non-zero and `set -e` would kill the launcher. Invoking the function as
    part of an `||` list is what suspends `set -e` for its body. Verified
    empirically: with `|| true` the failing assignment is survived, without
    it bash exits 1.
    """
    src = LAUNCHER.read_text()
    assert re.search(r"^_warn_if_leerie_stale \|\| true$", src,
                     re.MULTILINE), (
        "launcher must invoke _warn_if_leerie_stale, and with `|| true` — "
        "that suspends `set -e` for the function body, without which a "
        "failing `git show ... | awk` pipeline (pipefail) aborts the run")


def test_guard_runs_before_the_container_starts():
    """A warning that arrives after the run has begun is useless."""
    src = LAUNCHER.read_text()
    call = src.index("\n_warn_if_leerie_stale || true")
    assert call < src.index("host preflight: git repo + gh + jq")
