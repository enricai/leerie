"""Review round 17 H2: a setting a worker makes can no longer change what
I1's checker reads (DESIGN §8 *The settings are leerie's*).

The round's driver (review-r17-proofs/exec/test_zz_r17e_config_hides_dirt)
rewritten to assert the fix, for both checker halves and both restore
halves: `core.fileMode=false` and a clean filter in the shared
`info/attributes` are put back before the checker reads (round 18 H1: a
worker's settings are its environment, never its dirt), so what they hid
is the file's own line; a filter set, staged
through and removed again leaves only the index's stat cache, which the
fresh read does not trust; a change to the global config made after leerie
copied it changes nothing; and leerie's own command does not keep a
settings change (I2).
"""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

RUN_ID = "r17settings0"
BRANCH = f"leerie/runs/{RUN_ID}"
SUB = f"leerie/subtasks/{RUN_ID}/feat-001"
LIB = Path(__file__).resolve().parents[1] / "scripts" / "worktree-lib.sh"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True).stdout.strip()


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_id = RUN_ID
        self.run_dir = run_dir
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


@pytest.fixture()
def trees(tmp_path, monkeypatch, leerie):
    gcfg = tmp_path / "home-gitconfig"
    gcfg.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    _git(user, "config", "user.email", "t@x")
    _git(user, "config", "user.name", "t")
    (user / "app.py").write_text("ANSWER = 1\n")
    (user / "run.sh").write_text("#!/bin/sh\necho ok\n")
    os.chmod(user / "run.sh", 0o644)
    _git(user, "add", "-A")
    _git(user, "commit", "-qm", "base")
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    _git(repo, "branch", BRANCH, "main")
    _git(repo, "branch", SUB, BRANCH)
    staging = run_dir / "worktrees" / "staging"
    sub = run_dir / "worktrees" / "sub"
    _git(repo, "worktree", "add", "-q", str(staging), BRANCH)
    _git(repo, "worktree", "add", "-q", str(sub), SUB)
    return repo, staging, sub, gcfg


def _sh(fn: str, tree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", 'set -uo pipefail; . "$1"; shift; "$@"', "_",
         str(LIB), fn, str(tree), *args], capture_output=True, text=True)


def _one(leerie, tree: Path, branch: str, half: str) -> list[str]:
    """One checker half alone: each puts leerie's settings back itself."""
    if half == "python":
        return asyncio.run(leerie._tree_dirt(tree, branch=branch))
    sh = _sh("leerie_tree_dirt", tree)
    assert sh.returncode == 0, sh.stderr
    return sh.stdout.splitlines()


def _both(leerie, tree: Path, branch: str) -> list[str]:
    """Python's checker, asserted line for line equal to the shell's."""
    py = asyncio.run(leerie._tree_dirt(tree, branch=branch))
    sh = _sh("leerie_tree_dirt", tree)
    assert sh.returncode == 0, sh.stderr
    assert sh.stdout.splitlines() == py
    return py


def _age(path: Path) -> None:
    """Out of git's racy window: its stat is then trusted, as minutes into
    a worker's session it would be."""
    t = path.stat().st_mtime - 10
    os.utime(path, (t, t))


@pytest.mark.parametrize("half", ["python", "shell"])
def test_filemode_false_is_put_back_and_the_mode_shows(leerie, trees, half):
    repo, staging, sub, _g = trees
    os.chmod(sub / "run.sh", 0o755)
    _git(sub, "config", "core.fileMode", "false")    # writes the run's config
    assert _git(sub, "status", "--porcelain") == ""  # the worker's view
    # The checker puts leerie's config back before it reads, so the mode
    # change it hid is the tree's own line.
    assert _one(leerie, sub, SUB, half) == [" M run.sh"]
    assert _git(repo, "config", "--get", "core.fileMode") == "true"
    assert _both(leerie, sub, SUB) == [" M run.sh"]
    _git(sub, "config", "core.fileMode", "false")
    os.chmod(staging / "run.sh", 0o755)              # every tree shares it
    assert _one(leerie, staging, BRANCH, half) == [" M run.sh"]
    _git(sub, "config", "core.fileMode", "false")
    head = _git(sub, "rev-parse", "HEAD")
    if half == "python":
        assert asyncio.run(leerie._restore_tree(
            sub, head, where="t", branch=SUB)) is True
    else:
        p = _sh("leerie_reset_worktree", sub, SUB, head)
        assert p.returncode == 0, p.stderr
    assert not os.stat(sub / "run.sh").st_mode & 0o111
    assert _git(repo, "config", "--get", "core.fileMode") == "true"
    assert _both(leerie, sub, SUB) == []


@pytest.mark.parametrize("half", ["python", "shell"])
def test_a_clean_filter_in_the_shared_attributes_is_restored(
        leerie, trees, half):
    repo, staging, sub, _g = trees
    (sub / "app.py").write_text("ANSWER = 42\n")
    _age(sub / "app.py")
    common = Path(_git(sub, "rev-parse", "--path-format=absolute",
                       "--git-common-dir"))
    (common / "info" / "attributes").write_text("app.py filter=keep\n")
    _git(sub, "config", "filter.keep.clean",
         "sh -c 'cat >/dev/null; git show HEAD:\"$1\"' -- %f")
    _git(sub, "add", "app.py")
    assert _git(sub, "status", "--porcelain") == ""
    # Put back first: the edit the filter hid is the file's own line, and
    # the shared attributes file is gone.
    assert _one(leerie, sub, SUB, half) == [" M app.py"]
    assert not (common / "info" / "attributes").exists()
    (common / "info" / "attributes").write_text("app.py filter=keep\n")
    _git(sub, "config", "filter.keep.clean",
         "sh -c 'cat >/dev/null; git show HEAD:\"$1\"' -- %f")
    head = _git(sub, "rev-parse", "HEAD")
    if half == "python":
        assert asyncio.run(leerie._restore_tree(
            sub, head, where="t", branch=SUB)) is True
    else:
        p = _sh("leerie_reset_worktree", sub, SUB, head)
        assert p.returncode == 0, p.stderr
    assert (sub / "app.py").read_text() == "ANSWER = 1\n"
    assert not (common / "info" / "attributes").exists()
    assert _both(leerie, sub, SUB) == []


@pytest.mark.parametrize("half", ["python", "shell"])
def test_a_filter_set_and_removed_leaves_only_the_stat_cache(
        leerie, trees, half):
    """Settings equal to leerie's again, and the index still vouches for
    the edited file: status and `reset --hard` both trust it."""
    repo, staging, sub, _g = trees
    (sub / "app.py").write_text("ANSWER = 42\n")
    _age(sub / "app.py")
    common = repo
    (common / "info" / "attributes").write_text("app.py filter=keep\n")
    _git(sub, "config", "filter.keep.clean",
         "sh -c 'cat >/dev/null; git show HEAD:\"$1\"' -- %f")
    _git(sub, "add", "app.py")
    (common / "info" / "attributes").unlink()
    _git(sub, "config", "--unset", "filter.keep.clean")
    _git(sub, "config", "--remove-section", "filter.keep", check=False)
    assert _git(sub, "status", "--porcelain") == ""
    assert leerie._settings_dirt(sub) == []
    assert _both(leerie, sub, SUB) == [" M app.py"]
    head = _git(sub, "rev-parse", "HEAD")
    if half == "python":
        assert asyncio.run(leerie._restore_tree(
            sub, head, where="t", branch=SUB)) is True
    else:
        p = _sh("leerie_reset_worktree", sub, SUB, head)
        assert p.returncode == 0, p.stderr
    assert (sub / "app.py").read_text() == "ANSWER = 1\n"
    assert _both(leerie, sub, SUB) == []


def test_the_global_config_and_ignore_are_read_from_leeries_copy(
        leerie, trees, tmp_path):
    """Written after leerie copied them (once per process): the worker's
    own git sees the change, the checker does not."""
    repo, staging, sub, gcfg = trees
    gx = tmp_path / "xdg" / "git" / "ignore"
    gx.parent.mkdir(parents=True)
    gx.write_text("helper.py\n")                    # the default ignore file
    (sub / "helper.py").write_text("H = 1\n")
    assert _git(sub, "status", "--porcelain") == ""
    assert _both(leerie, sub, SUB) == ["?? helper.py"]
    gx2 = tmp_path / "other-ignore"
    gx2.write_text("other.py\n")
    gcfg.write_text(f"[core]\n\texcludesFile = {gx2}\n")
    (sub / "other.py").write_text("O = 1\n")
    assert "other.py" not in _git(sub, "status", "--porcelain")
    assert _both(leerie, sub, SUB) == ["?? helper.py", "?? other.py"]


@pytest.mark.parametrize("half", ["python", "shell"])
def test_a_global_filter_written_later_hides_nothing(leerie, trees, half):
    """A committed `.gitattributes` names a filter no config defines; the
    worker defines it in its global config, stages through it and leaves
    the edit. Its own git sees nothing; the checker reads leerie's copy of
    the global config, which has no such filter, and the restore too."""
    repo, staging, sub, gcfg = trees
    (sub / ".gitattributes").write_text("app.py filter=keep\n")
    _git(sub, "add", ".gitattributes")
    _git(sub, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "attrs")
    (sub / "app.py").write_text("ANSWER = 42\n")
    _age(sub / "app.py")
    subprocess.run(["git", "config", "-f", str(gcfg), "filter.keep.clean",
                    "sh -c 'cat >/dev/null; git show HEAD:\"$1\"' -- %f"],
                   check=True)
    _git(sub, "add", "app.py")
    assert _git(sub, "status", "--porcelain") == ""
    assert _both(leerie, sub, SUB) == [" M app.py"]
    head = _git(sub, "rev-parse", "HEAD")
    if half == "python":
        assert asyncio.run(leerie._restore_tree(
            sub, head, where="t", branch=SUB)) is True
    else:
        p = _sh("leerie_reset_worktree", sub, SUB, head)
        assert p.returncode == 0, p.stderr
    assert (sub / "app.py").read_text() == "ANSWER = 1\n"


def test_a_global_ignore_copied_with_the_run_is_honoured(leerie, tmp_path,
                                                        monkeypatch):
    """The other direction: what the developer's global ignore covered when
    leerie copied it stays ignored for every reader."""
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "ignore").write_text("*.log\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "none"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / "a.py").write_text("x\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "b")
    run_dir = tmp_path / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    assert (run_dir / "repo-settings" / "host.exclude").read_text() \
        == "*.log\n"
    _git(repo, "branch", BRANCH, "main")
    stg = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(stg), BRANCH)
    (stg / "debug.log").write_text("x\n")
    (xdg / "git" / "ignore").write_text("")          # changed afterwards
    assert _both(leerie, stg, BRANCH) == []


def test_a_missing_copy_is_named(leerie, trees):
    """Nothing to put back from: the one settings line that remains."""
    repo, staging, sub, _g = trees
    (repo.parent / "repo-settings" / "config").unlink()
    line = (f"git settings unknown: leerie's copy "
            f"{repo.parent / 'repo-settings'} of the run repository's "
            "settings is missing")
    assert _both(leerie, sub, SUB) == [line]


def test_an_automatic_gc_does_not_change_the_settings(leerie, trees):
    """`git gc` writes `info/refs` unless `repack.updateServerInfo` is
    off, which `_ensure_run_repo` sets before it copies the settings."""
    repo, staging, sub, _g = trees
    (sub / "b.py").write_text("y\n")
    _git(sub, "add", "b.py")
    _git(sub, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "b")
    _git(repo, "gc", "-q")
    assert not (repo / "info" / "refs").exists()
    assert _both(leerie, sub, SUB) == []


# Every key DESIGN §8 names as changing what git reports: written from a
# worker's tree, each is put back by either checker half before it reads —
# no settings line, the config byte-identical to leerie's copy — and by the
# one restore.
KEYS = [
    ("core.fileMode", "false"), ("core.autocrlf", "true"),
    ("core.eol", "crlf"), ("core.safecrlf", "false"),
    ("core.ignoreCase", "true"), ("core.precomposeUnicode", "true"),
    ("core.symlinks", "false"), ("core.checkStat", "minimal"),
    ("core.trustctime", "false"), ("diff.ignoreSubmodules", "all"),
    ("submodule.x.ignore", "all"), ("filter.x.clean", "cat"),
    ("core.attributesFile", "/tmp/a"), ("core.excludesFile", "/tmp/e"),
    ("core.sparseCheckout", "true"), ("core.fsmonitor", "true"),
    ("core.untrackedCache", "true"), ("status.showUntrackedFiles", "no"),
    ("extensions.worktreeConfig", "true"), ("include.path", "/tmp/i"),
]


@pytest.mark.parametrize("key,value", KEYS, ids=[k for k, _v in KEYS])
@pytest.mark.parametrize("half", ["python", "shell"])
def test_every_key_is_put_back_and_restored(leerie, trees, key, value, half):
    repo, staging, sub, _g = trees
    copy = (repo.parent / "repo-settings" / "config").read_bytes()
    _git(sub, "config", key, value)
    lines = _one(leerie, sub, SUB, half)
    assert not any(ln.startswith("git settings") for ln in lines), lines
    assert (repo / "config").read_bytes() == copy
    _git(sub, "config", key, value)
    head = _git(sub, "rev-parse", "HEAD")
    assert asyncio.run(leerie._restore_tree(
        sub, head, where="t", branch=SUB)) is True
    assert (repo / "config").read_bytes() == copy


def test_leeries_own_command_does_not_keep_a_settings_change(leerie, trees):
    """I2: husky's install runs `git config core.hooksPath .husky/_`."""
    repo, staging, sub, _g = trees
    copy = (repo / "config").read_bytes()

    async def install() -> dict:
        async with leerie._leaves_tree_as_found(staging, "install/build",
                                                "t") as rec:
            _git(staging, "config", "core.hooksPath", ".husky/_")
        return rec
    asyncio.run(install())
    assert (repo / "config").read_bytes() == copy
    assert _both(leerie, staging, BRANCH) == []


def test_a_settings_change_alone_is_never_reasked(leerie, trees):
    """Round 18 H1: husky's install sets `core.hooksPath`. The gate puts it
    back and finds the tree clean; the worker is never asked."""
    repo, staging, sub, _g = trees
    copy = (repo / "config").read_bytes()
    _git(sub, "config", "core.hooksPath", ".husky/_")
    notes: list[str] = []

    async def reask(note: str) -> None:
        notes.append(note)
    left = asyncio.run(leerie._clean_tree_gate(
        sub, caps=dict(leerie.DEFAULT_CAPS), label="t", reask=reask,
        branch=SUB, base=None))
    assert left == [] and notes == []
    assert (repo / "config").read_bytes() == copy
