"""Review round 18 H1: a worker's change to its tree's repository settings
is its environment, put back by leerie, never its dirt (DESIGN §8 *The
settings are leerie's*).

husky's install, which every `pnpm install` in a husky repository runs
through `prepare`, sets `core.hooksPath`. The checker used to count that as
"git settings changed", which no re-ask could clear, so the subtask's work
was rolled back. Leerie now copies its settings back at the worker's exit
and before every read, so a husky install keeps the work, and a setting
that would hide a change (`core.fileMode=false`) hides nothing: the change
is the file's own line.

Real `_ensure_run_repo`, real `setup-run.sh` and `new-worktree.sh`, real
git, and the real husky 9 package run by node: `LEERIE_TEST_HUSKY_DIR`
names an installed `husky` package directory, binding when set, as
`LEERIE_TEST_TYPESCRIPT_DIR` is; unset, that test is skipped.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_run_repo import RUN_ID, _git, _St

pytestmark = pytest.mark.real_planning_worktree

LIB = Path(__file__).resolve().parents[1] / "scripts" / "worktree-lib.sh"
HUSKY_ENV = "LEERIE_TEST_HUSKY_DIR"


def _husky_dir() -> Path:
    env = os.environ.get(HUSKY_ENV)
    if env:
        assert (Path(env) / "bin.js").is_file(), f"{HUSKY_ENV}={env}"
        return Path(env)
    pytest.skip(f"no husky package ({HUSKY_ENV} unset)")


def _user_repo(tmp_path: Path, husky_installed: bool) -> Path:
    r = tmp_path / "user"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    (r / "package.json").write_text('{"scripts": {"prepare": "husky"}}\n')
    (r / ".gitignore").write_text("node_modules/\n")
    (r / ".husky").mkdir()
    (r / ".husky" / "pre-commit").write_text("true\n")
    (r / "run.sh").write_text("#!/bin/sh\necho ok\n")
    os.chmod(r / "run.sh", 0o644)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    if husky_installed:
        # What the developer's own install left: git's own spelling.
        _git(r, "config", "core.hooksPath", ".husky/_")
    return r


def _tree(leerie, tmp_path, monkeypatch, husky_installed: bool):
    user = _user_repo(tmp_path, husky_installed)
    state = tmp_path / "state"
    monkeypatch.chdir(user)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    st = _St(user, state)
    asyncio.run(leerie._ensure_run_repo_or_die(st))
    p = asyncio.run(leerie._run_script("setup-run.sh", st.run_id, "main",
                                       st=st))
    assert p.returncode == 0, p.stderr
    p = asyncio.run(leerie._run_script("new-worktree.sh", "feat-x", RUN_ID,
                                       st=st))
    assert p.returncode == 0, p.stderr
    wt = Path(p.stdout.strip().splitlines()[-1])
    branch = _git(wt, "symbolic-ref", "--short", "HEAD")
    return st, wt, branch


def _gate(leerie, wt: Path, branch: str) -> tuple[list[str], list[str]]:
    notes: list[str] = []

    async def reask(note: str) -> None:
        notes.append(note)
    left = asyncio.run(leerie._clean_tree_gate(
        wt, caps=dict(leerie.DEFAULT_CAPS), label="t", reask=reask,
        branch=branch, base=None))
    return left, notes


def _config(wt: Path) -> Path:
    return Path(_git(wt, "rev-parse", "--path-format=absolute",
                     "--git-common-dir")) / "config"


@pytest.mark.parametrize("husky_installed", [True, False],
                         ids=["user-has-husky", "user-has-no-hooks-path"])
def test_a_real_husky_prepare_keeps_the_work(leerie, tmp_path, monkeypatch,
                                             husky_installed):
    """The worker installs (husky's `prepare` sets `core.hooksPath`),
    commits its work, and exits. Leerie puts its settings back, takes the
    branch, and the gate finds the tree clean: no re-ask, nothing rolled
    back, the commit in the run repository."""
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    husky = _husky_dir()
    st, wt, branch = _tree(leerie, tmp_path, monkeypatch, husky_installed)
    copy = (st.run_dir / "repo-settings" / "config").read_bytes()
    shutil.copytree(husky, wt / "node_modules" / "husky")
    before = _config(wt).read_bytes()
    # `"prepare": "husky"` runs husky's bin, in the package's directory.
    p = subprocess.run(["node", str(wt / "node_modules" / "husky" /
                                    "bin.js")], cwd=wt, capture_output=True,
                       text=True, env={**os.environ, "HUSKY": ""})
    assert p.returncode == 0, p.stderr
    assert _config(wt).read_bytes() != before          # husky wrote it
    assert _git(wt, "config", "core.hooksPath") == ".husky/_"
    (wt / "feature.py").write_text("F = 1\n")
    _git(wt, "add", "feature.py")
    _git(wt, "commit", "-qm", "feature")
    head = _git(wt, "rev-parse", "HEAD")
    asyncio.run(leerie._take_after_worker(wt))
    assert _config(wt).read_bytes() == copy            # put back
    left, notes = _gate(leerie, wt, branch)
    assert left == [] and notes == []
    run_repo = st.run_dir / "repo"
    assert _git(run_repo, "rev-parse", f"refs/heads/{branch}") == head
    assert (wt / "feature.py").read_text() == "F = 1\n"


@pytest.mark.parametrize("half", ["python", "shell"])
def test_filemode_false_cannot_hide_a_mode_change(leerie, tmp_path,
                                                  monkeypatch, half):
    """The worker makes `run.sh` executable, commits nothing, and turns
    `core.fileMode` off: its own git shows nothing. The checker puts
    leerie's settings back first, so the mode change is the tree's own
    line, the re-ask names it, and nothing about settings is asked."""
    st, wt, branch = _tree(leerie, tmp_path, monkeypatch, False)
    os.chmod(wt / "run.sh", 0o755)
    _git(wt, "config", "core.fileMode", "false")
    assert _git(wt, "status", "--porcelain") == ""     # the worker's view
    if half == "python":
        lines = asyncio.run(leerie._tree_dirt(wt))
    else:
        p = subprocess.run(
            ["bash", "-c", 'set -uo pipefail; . "$1"; leerie_tree_dirt "$2"',
             "_", str(LIB), str(wt)], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        lines = p.stdout.splitlines()
    assert lines == [" M run.sh"]
    assert _git(wt, "config", "core.fileMode") == "true"
    _git(wt, "config", "core.fileMode", "false")       # again, then exit
    asyncio.run(leerie._take_after_worker(wt))
    left, notes = _gate(leerie, wt, branch)
    assert left == [" M run.sh"]
    assert notes and " M run.sh" in notes[0]
    assert "git settings" not in notes[0]


def test_settings_leerie_cannot_put_back_stop_the_run(leerie, tmp_path,
                                                      monkeypatch):
    """Without leerie's copy nothing can be put back. That is leerie's own
    state, not the worker's: the gate stops instead of re-asking, and so
    does the worker's exit."""
    st, wt, branch = _tree(leerie, tmp_path, monkeypatch, False)
    (st.run_dir / "repo-settings" / "config").unlink()
    with pytest.raises(SystemExit):
        _gate(leerie, wt, branch)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._take_after_worker(wt))
