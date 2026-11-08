"""The measurement grant's kinds and its directory's lifetime (DESIGN §6
*Leerie's runs of the repository's commands are confined too*).

- A measurement directory belongs to one tree for that tree's lifetime: a
  tree made again at the same path gets a fresh one, and the old one is
  swept.
- The toolchain kind (`mise install`) trusts nothing: its mise state is
  emptied first. The setup-hook kind writes where DESIGN §6½ says a hook
  installs; the plain measurement kind writes neither.
- `claude_p` records the first committing worker's start, which the setup
  hook's premise reads.
- A confined worker reads the run's mise overrides, which every `mise` call
  of a Go repository's run loads.
- What a setup hook installs reaches a worker through `~/.local/bin` alone
  (DESIGN §6½ step 1)."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import needs_landlock, skip_without_landlock, fake_run_dir
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _grants, _probe, run)
from tests.conftest import run_git_out  # noqa: E402



_git = run_git_out


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_id = run_dir.name
        self.run_dir = run_dir
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


@pytest.fixture()
def layout(leerie, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / "app.py").write_text("X = 1\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "state" / "runs" / "lifetime0001"
    run_dir.mkdir(parents=True)
    st = _St(user, run_dir)
    repo = leerie._ensure_run_repo(st)
    return SimpleNamespace(st=st, repo=repo, run_dir=run_dir)


def _add(repo: Path, wt: Path) -> Path:
    _git(repo, "worktree", "add", "-q", "--detach", str(wt), "main")
    return wt


def _remove(repo: Path, wt: Path) -> None:
    _git(repo, "worktree", "remove", "--force", str(wt))


def test_a_tree_made_again_gets_a_fresh_measurement_dir(leerie, layout):
    view = layout.run_dir / "worktrees" / "lifetime0001-view-x"
    _add(layout.repo, view)
    first = leerie._measure_dir(layout.st, view)
    first_tmp = leerie._private_tmp(first)
    (first / "reports" / "left-behind").write_text("from the first tree\n")
    (first_tmp / "left-behind").write_text("from the first tree\n")
    assert leerie._measure_dir(layout.st, view) == first   # stable
    _remove(layout.repo, view)
    _add(layout.repo, view)
    second = leerie._measure_dir(layout.st, view)
    assert second != first
    assert second.parent == first.parent == layout.run_dir / "measure"
    assert list((second / "reports").iterdir()) == []
    second_tmp = leerie._private_tmp(second)
    assert second_tmp != first_tmp and list(second_tmp.iterdir()) == []
    # The first tree's directory, and its private temp directory, were
    # swept when the second was made.
    assert not first.exists()
    assert not first_tmp.exists()


def test_a_removed_trees_dir_is_swept_by_the_next_measurement(leerie,
                                                              layout):
    a = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-a")
    b = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-b")
    da = leerie._measure_dir(layout.st, a)
    _remove(layout.repo, a)
    assert da.exists()
    leerie._measure_dir(layout.st, b)
    assert not da.exists()


def test_two_trees_never_share_a_measurement_dir(leerie, layout):
    a = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-a")
    b = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-b")
    assert leerie._measure_dir(layout.st, a) != \
        leerie._measure_dir(layout.st, b)


def test_the_kinds_differ_only_where_designed(leerie, layout, tmp_path,
                                              monkeypatch):
    home, mise = tmp_path / "home", tmp_path / "mise-data"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MISE_DATA_DIR", str(mise))
    tree = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-p")
    bin_ = str((home / ".local" / "bin").resolve())
    data = str(mise.resolve())
    # The toolchain kinds make their directories first; the plain grant is
    # derived after, so its reads see the same filesystem.
    h = leerie._measurement_grants(layout.st, tree, leerie.SETUP_HOOK_KIND)
    t = leerie._measurement_grants(layout.st, tree, leerie.TOOLCHAIN_KIND)
    m = leerie._measurement_grants(layout.st, tree)
    assert bin_ not in m.rw and data not in m.rw
    assert bin_ in h.rw and data in h.rw
    assert data in t.rw and bin_ not in t.rw
    # Every other grant is the same for all three — compared without the
    # reads `_beside` lists outside the state root: those are the live
    # entries of the directories on the root's path (`/tmp`, pytest's
    # basetemp), which other processes — `-n 4`'s other workers among
    # them — add to and remove from between the three calls.
    root = layout.run_dir.parent.parent.resolve()
    above = {str(a) for a in root.parents}

    def own(paths):
        return {p for p in paths if str(Path(p).parent) not in above}
    for g in (h, t):
        assert own(g.rw) - {bin_, data} == own(m.rw)
        assert own(g.ro) == own(m.ro)
    with pytest.raises(ValueError):
        leerie._measurement_grants(layout.st, tree, "anything")


def test_the_toolchain_trusts_nothing_the_hook_left(leerie, layout):
    """The hook and `mise install` share a checkout, so a measurement dir:
    whatever the hook left in mise's state (its trust records) is gone
    before `mise install` runs."""
    tree = _add(layout.repo, layout.run_dir / "worktrees" / "lifetime0001-p")
    h = leerie._measurement_grants(layout.st, tree, leerie.SETUP_HOOK_KIND)
    state = Path(dict(h.env)["MISE_STATE_DIR"])
    (state / "trusted-configs").mkdir(parents=True)
    (state / "trusted-configs" / "planted").write_text("x")
    t = leerie._measurement_grants(layout.st, tree, leerie.TOOLCHAIN_KIND)
    assert dict(t.env)["MISE_STATE_DIR"] == str(state)
    assert state.is_dir() and list(state.iterdir()) == []


def test_a_tree_holding_the_run_directory_is_refused(leerie, layout):
    with pytest.raises(SystemExit):
        leerie._measurement_grants(layout.st, layout.run_dir.parent)
    with pytest.raises(SystemExit):
        leerie._measurement_grants(layout.st, layout.run_dir / "acceptance")


def _claude_p(leerie, monkeypatch, tmp_path, schema_key: str) -> dict:
    """Drive `claude_p` to its spawn and stop there; return the state, with
    `reached_spawn` set when the spawn was reached (so an absent flag is
    not a call that failed earlier)."""
    reached: dict = {}

    async def stop(*_a, **_k):
        reached["spawn"] = True
        raise RuntimeError("stop at spawn")
    monkeypatch.setattr(leerie, "_invoke", stop)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    data = {"verbosity": "quiet"}
    st = SimpleNamespace(
        path=fake_run_dir() / "state.json", run_dir=fake_run_dir(),
        repo_root=tmp_path / "user", data=data, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None, save=lambda: None)
    cwd = tmp_path / "tree"
    cwd.mkdir(exist_ok=True)
    with pytest.raises(Exception):
        asyncio.run(leerie.claude_p(
            "u", "s", schema_key=schema_key, cwd=str(cwd),
            allowed_tools=leerie.ACT_TOOLS if schema_key == "implementer"
            else "Read",
            max_turns=5, autonomous=schema_key == "implementer",
            caps=dict(leerie.DEFAULT_CAPS), st=st, model="sonnet",
            sid=f"{schema_key}-t"))
    assert reached.get("spawn"), schema_key
    return data


def test_claude_p_records_the_first_committing_worker(leerie, monkeypatch,
                                                      tmp_path):
    judge = _claude_p(leerie, monkeypatch, tmp_path, "delivery_judge")
    assert "committing_worker_started" not in judge
    impl = _claude_p(leerie, monkeypatch, tmp_path, "implementer")
    assert impl["committing_worker_started"] is True


@needs_landlock
@skip_without_landlock
def test_a_confined_worker_reads_the_runs_mise_overrides(leerie, run,
                                                         tmp_path):
    """A Go repository's run names `<run>/mise-overrides.toml` to every
    process (`MISE_OVERRIDE_CONFIG_FILENAMES`); a confined worker must be
    able to load it, or its `mise` cannot."""
    overrides = run["run_dir"] / leerie.MISE_OVERRIDES_NAME
    overrides.write_text('[tools]\ngo = "1.22.5"\n')
    g = _grants(leerie, run)
    assert str(overrides) in g.ro
    env = {**run["env"], "MISE_OVERRIDE_CONFIG_FILENAMES": str(overrides)}
    got = _probe(leerie, g, f"read overrides|cat {overrides}", env)
    assert got == {"read overrides": True}
    if shutil.which("mise") is None:
        return
    # The real loader, where the host has it (the image does).
    r = subprocess.run(
        leerie._confined_argv(g, ["mise", "ls", "--current"]),
        cwd=run["wts"]["wt-a"], capture_output=True, text=True,
        env={**os.environ, **env})
    assert r.returncode == 0, r.stderr
    assert "go" in r.stdout


@needs_landlock
@skip_without_landlock
def test_a_hook_reaches_a_worker_through_local_bin_alone(leerie, run,
                                                         tmp_path,
                                                         monkeypatch):
    """DESIGN §6½ step 1, through the real hook: the recipe's tool is
    written beneath `~/.local/bin` and a confined worker imports it. The
    hook's pip user base is its checkout's, a worker's is another and no
    worker reads the hook's. `core.hooksPath` is set and `.git` is a file,
    which is what `pre-commit install` and a write to `.git/hooks` meet."""
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    run_dir = run["run_dir"]
    user = tmp_path / "user"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    monkeypatch.setenv("PATH", f"{os.environ['PATH']}:{local_bin}")
    monkeypatch.setenv("PYTHONUSERBASE", str(run_dir / "worktrees" / ".stores" / "stores" / "pyuser"))
    tree = _add(run_dir / "repo", run_dir / "worktrees" / f"{run_dir.name}-p")
    hook = tree / ".leerie-setup.sh"
    hook.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'lib="$HOME/.local/bin/.tool"\n'
        'mkdir -p "$lib"\n'
        'echo "X = 1" > "$lib/tool_mod.py"\n'
        'echo "$PYTHONUSERBASE" > "$lib/user-base"\n'
        'git config core.hooksPath > "$lib/hooks-path"\n')
    st = _St(user, run_dir)
    asyncio.run(leerie._run_setup_hook(tree, run_dir / "logs", st))
    assert st.data["provision"]["sh_hook_ran"] is True
    lib = local_bin / ".tool"

    hook_base = (lib / "user-base").read_text().strip()
    assert hook_base == str(run_dir / "worktrees" / ".stores" / "tree-stores" / tree.name / "pyuser")
    assert Path(hook_base).is_dir()
    assert (lib / "hooks-path").read_text().strip() == str(
        user / ".git" / "hooks")
    assert (tree / ".git").is_file()

    cache_env = leerie._worker_cache_env(os.environ, run_dir)
    assert cache_env["PYTHONUSERBASE"] != hook_base
    assert str(local_bin) in cache_env["PATH"].split(os.pathsep)
    env = {**run["env"], "HOME": str(home)}
    g = _grants(leerie, {**run, "env": env})
    got = _probe(leerie, g, f"""
import|PYTHONPATH={lib} python3 -c 'import tool_mod'
hook base|ls {hook_base}
overwrite|echo x >> {lib / "tool_mod.py"}
""", env)
    assert got == {"import": True, "hook base": False, "overwrite": False}
