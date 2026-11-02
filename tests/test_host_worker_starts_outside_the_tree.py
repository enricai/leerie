"""DESIGN §6 *The host never runs a program the checkout chose*, part 4
(review round 27, isolation I1).

`host_finalize` runs the rebaser in a worktree of the run branch, whose
content the workers wrote and nobody merged. `claude_p(cwd=<worktree>)`
started the bare name `claude`, unconfined, with that worktree as its working
directory: a version manager's shim for `claude` (or for the `node` its
`#!/usr/bin/env node` names) chooses the program from the cwd's
`.tool-versions`, so a committed `nodejs path:/proc/self/cwd/.evil` plus
`.evil/bin/claude` ran the workers' program on the host at finalize, with
the operator's credentials.

The reviewer's proof, inverted on the shipped `run_rebaser`: the same
builtins-only asdf-like shim (one name) and the same plant; the plant never
runs, the CLI starts outside the worktree and is given it by `--add-dir`,
and the directory it started in is gone afterwards. Its control starts the
same shim in the worktree and is caught.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_repo_first as _git  # noqa: E402

# An asdf-like shim: shell builtins only. It records where it started, then
# takes the program from the cwd's `.tool-versions`.
_SHIM = """#!/bin/sh
printf '%s\\n' "$PWD" >> "$R27_STARTS"
while read -r tool ver; do
  case "$tool" in
    nodejs) case "$ver" in path:*) exec "${ver#path:}/bin/claude" "$@" ;; esac ;;
  esac
done < ./.tool-versions
exit 97
"""

_PLANT = """#!/bin/sh
printf '%s\\n' "$PWD" >> "$R27_MARK"
exit 3
"""


def _run_branch_tree(tmp_path: Path) -> Path:
    """A worktree as a worker could leave the run branch."""
    wt = tmp_path / "run-branch-worktree"
    wt.mkdir()
    _git(wt, "init", "-q", "-b", "main")
    _git(wt, "config", "user.email", "x@example.invalid")
    _git(wt, "config", "user.name", "x")
    (wt / ".tool-versions").write_text("nodejs path:/proc/self/cwd/.evil\n")
    (wt / ".evil" / "bin").mkdir(parents=True)
    plant = wt / ".evil" / "bin" / "claude"
    plant.write_text(_PLANT)
    plant.chmod(0o755)
    _git(wt, "add", "-A")
    _git(wt, "commit", "-qm", "feature")
    return wt


@pytest.fixture
def writable_tmp(tmp_path: Path):
    """The run's CLI configuration template is made read-only; make the
    tree writable again so pytest can remove it."""
    yield tmp_path
    for d, dirs, files in os.walk(tmp_path):
        for n in [*dirs, *files]:
            p = os.path.join(d, n)
            if not os.path.islink(p):
                os.chmod(p, stat.S_IMODE(os.lstat(p).st_mode) | 0o700)


def test_the_rebaser_cli_is_never_chosen_by_the_run_branch(
        leerie, writable_tmp: Path, monkeypatch) -> None:
    tmp_path = writable_tmp
    wt = _run_branch_tree(tmp_path)
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "claude").write_text(_SHIM)
    (shims / "claude").chmod(0o755)
    mark, starts = tmp_path / "planted-ran", tmp_path / "shim-starts"
    monkeypatch.setenv("R27_MARK", str(mark))
    monkeypatch.setenv("R27_STARTS", str(starts))
    monkeypatch.setenv("PATH", f"{shims}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.chdir("/")
    # One real spawn is the proof; a retry's backoff only slows it down.
    real_invoke, calls = leerie._invoke, []

    async def _once(*a, **k):
        calls.append((list(a[0]), a[1]))
        if len(calls) > 1:
            raise RuntimeError("one spawn is enough")
        return await real_invoke(*a, **k)
    monkeypatch.setattr(leerie, "_invoke", _once)

    result = leerie.run_rebaser(tmp_path / "state", wt, "r27", wt,
                                "main", "main", "main")

    assert result["status"] == "failed"
    assert not mark.exists(), mark.read_text()
    argv, cwd = calls[0]
    real_wt = os.path.realpath(wt)
    assert not (os.path.realpath(cwd) + "/").startswith(real_wt + "/"), cwd
    assert argv[argv.index("--add-dir") + 1] == str(wt)
    # The CLI was found from `/`, by absolute path, and started where the
    # call said; nothing was started in the worktree.
    started = starts.read_text().split()
    assert started and cwd in started, (started, cwd)
    assert not any((os.path.realpath(s) + "/").startswith(real_wt + "/")
                   for s in started), started
    assert not Path(cwd).exists(), "the start directory outlived the call"
    # Control: the same shim started in the worktree runs the plant.
    subprocess.run(["claude"], cwd=wt, timeout=60,
                   env={**os.environ, "PATH": f"{shims}:/usr/bin:/bin"})
    assert mark.read_text().split() == [str(wt)]


def test_the_rebaser_is_told_the_worktree(leerie, writable_tmp: Path,
                                          monkeypatch) -> None:
    """The worker starts outside the worktree, so its prompt names it."""
    wt = _run_branch_tree(writable_tmp)
    monkeypatch.setenv("HOME", str(writable_tmp / "home"))
    seen = {}

    async def _fake(**kw):
        seen.update(kw)
        raise RuntimeError("stop")
    monkeypatch.setattr(leerie, "claude_p", _fake)
    leerie.run_rebaser(writable_tmp / "state", wt, "r27", wt,
                       "main", "main", "main")
    assert f"The disposable worktree: {wt}\n" in seen["user_prompt"]
    assert seen["add_dirs"] == [str(wt)]
    assert seen["cwd"] != str(wt)


def test_a_relative_path_entry_never_finds_the_cli_in_the_cwd(
        leerie, tmp_path: Path, monkeypatch) -> None:
    """`.` or `node_modules/.bin` on PATH names a directory below wherever
    the CLI starts; `_exe_from_root` anchors it at `/`."""
    co = tmp_path / "checkout"
    (co / "node_modules" / ".bin").mkdir(parents=True)
    for p in (co / "claude", co / "node_modules" / ".bin" / "claude"):
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)
    real = tmp_path / "bin"
    real.mkdir()
    (real / "claude").write_text("#!/bin/sh\nexit 0\n")
    (real / "claude").chmod(0o755)
    monkeypatch.chdir(co)
    monkeypatch.setenv("PATH", f".:node_modules/.bin::{real}")
    assert leerie._exe_from_root("claude") == str(real / "claude")
    monkeypatch.setenv("PATH", ".:node_modules/.bin")
    assert leerie._exe_from_root("claude") == "claude"
    # Control: resolved from the checkout, the relative entry finds it.
    import shutil
    assert shutil.which("claude", path=".") == "./claude"


def test_the_start_directory_is_never_inside_a_tree(leerie, tmp_path: Path,
                                                    monkeypatch) -> None:
    d = leerie._host_worker_cwd(tmp_path / "elsewhere")
    try:
        assert d.is_dir() and not any(d.iterdir())
        assert stat.S_IMODE(d.stat().st_mode) == 0o700
    finally:
        d.rmdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    with pytest.raises(OSError):
        leerie._host_worker_cwd(tmp_path)
    assert not [p for p in tmp_path.iterdir()
                if p.name.startswith("leerie-host-cwd-")]
