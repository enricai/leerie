"""A repository's build, lint and test commands are read from it (DESIGN §6½
*A repository's build, lint and test commands are read from it*).

An undeclared axis came from a table of files to commands: `pyproject.toml`
gave a bare `pytest`, which is not on PATH after `uv sync` (lane LG2's uv
launch: "pytest: command not found" with uv installed and the install
green), and a Zig repository got no test command at all, so its runs ended
`done` with no test ever run. The `blt_discovery` worker reads the commands
from the repository; code holds each to one program the run can run.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest


def _state(leerie, tmp_path, repo):
    root = tmp_path / ".leerie"
    (root / "runs" / "_t" / "logs").mkdir(parents=True)
    st = leerie.State(root, "_t")
    st.data = {"task": "t", "worker_count": 0,
               "planning_worktree": str(repo)}
    st.save()
    return st


def _exe(d: Path, name: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)
    return p


def _ans(build="", lint="", test=""):
    return {ax: {"command": c, "evidence": "x"}
            for ax, c in (("build", build), ("lint", lint), ("test", test))}


@pytest.fixture()
def blt_reset(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_DISCOVERED_BLT", None)


def test_there_is_no_table_of_files_to_commands(leerie, tmp_path, blt_reset):
    """Marker files alone name no command: before the worker has read the
    repository every undeclared axis is empty."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "go.mod").write_text("module x\n")
    assert leerie._infer_build_lint_test(tmp_path) == {
        "build": "", "lint": "", "test": ""}


def test_resolve_blt_reads_the_discovered_commands_under_declared_ones(
        leerie, tmp_path, monkeypatch, blt_reset):
    monkeypatch.setattr(leerie, "_DISCOVERED_BLT",
                        {"build": "", "lint": "ruff check .",
                         "test": "uv run pytest"})
    (tmp_path / ".leerie").mkdir()
    (tmp_path / ".leerie" / "config.toml").write_text('lint = ""\n')
    assert leerie.resolve_blt(tmp_path) == {
        "build": "", "lint": "", "test": "uv run pytest"}


@pytest.mark.parametrize("cmd,kind", [
    ("uv run pytest && echo ok", "SHELL_OPERATOR"),
    ("pytest | tee x", "SHELL_OPERATOR"),
    ("cd sub", "NO_PROGRAM"),
    ("FOO=1", "NO_PROGRAM"),
    ("'unterminated", "UNPARSEABLE"),
    ("poetry run pytest", "UNKNOWN_PROGRAM"),
])
def test_a_command_the_run_cannot_run_is_named(leerie, tmp_path, monkeypatch,
                                               cmd, kind):
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "md"))
    issues = leerie._blt_command_issues(_ans(test=cmd), tmp_path, [],
                                        str(tmp_path / "empty"))
    assert len(issues) == 1 and issues[0].startswith(kind), issues


def test_a_command_the_run_can_run_raises_nothing(leerie, tmp_path,
                                                  monkeypatch):
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "md"))
    uv = tmp_path / "md" / "installs" / "uv" / "0.13.8"
    _exe(uv, "uv")
    _exe(tmp_path / "usr-bin", "make")
    _exe(tmp_path, "gradlew")
    issues = leerie._blt_command_issues(
        _ans(build="CI=1 make", lint="./gradlew check",
             test="uv run pytest -q"),
        tmp_path, [str(uv)], str(tmp_path / "usr-bin"))
    assert issues == []


def test_discovery_asks_again_then_drops_an_axis_it_cannot_run(
        leerie, tmp_path, monkeypatch, blt_reset):
    """Driven through `_discover_blt`: a test command whose program the run
    lacks is re-asked; one still unrunnable after the rounds leaves its
    axis empty, and the rest is recorded and used."""
    repo = tmp_path / "repo"
    repo.mkdir()
    md = tmp_path / "md"
    zig = md / "installs" / "zig" / "0.13.0"
    _exe(zig, "zig")
    monkeypatch.setenv("MISE_DATA_DIR", str(md))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    st = _state(leerie, tmp_path, repo)
    st.data["provision"] = {"bin_paths": [str(zig)]}
    prompts: list[str] = []

    async def _fake(**kw):
        assert kw["schema_key"] == "blt_discovery"
        prompts.append(kw["user_prompt"])
        return _ans(build="zig build", test="make test")

    monkeypatch.setattr(leerie, "claude_p", _fake)
    caps = dict(leerie.DEFAULT_CAPS)
    asyncio.run(leerie._discover_blt(
        st, caps, {}, {}, repo,
        [{"kind": "install", "command": ["zig", "fetch"],
          "working_dir": "."}]))
    assert "(.) zig fetch" in prompts[0]
    assert len(prompts) >= 2
    assert "UNKNOWN_PROGRAM" in prompts[1]
    assert st.data["provision"]["blt"] == {
        "build": "zig build", "lint": "", "test": ""}
    assert leerie._infer_build_lint_test(repo) == {
        "build": "zig build", "lint": "", "test": ""}


def test_a_failed_worker_records_no_command(leerie, tmp_path, monkeypatch,
                                            blt_reset):
    repo = tmp_path / "repo"
    repo.mkdir()
    st = _state(leerie, tmp_path, repo)

    async def _boom(**kw):
        raise leerie.WorkerError("crashed")

    monkeypatch.setattr(leerie, "claude_p", _boom)
    asyncio.run(leerie._discover_blt(st, dict(leerie.DEFAULT_CAPS), {}, {},
                                     repo, []))
    assert st.data["provision"]["blt"] == {"build": "", "lint": "",
                                           "test": ""}


def test_resume_uses_the_recorded_commands(leerie, tmp_path, blt_reset):
    st = _state(leerie, tmp_path, tmp_path)
    st.data["provision"] = {"blt": {"build": "", "lint": "",
                                    "test": "mix test"}}
    leerie._resume_provision_env(st, {})
    assert leerie._infer_build_lint_test(tmp_path)["test"] == "mix test"


def test_the_worker_is_told_the_install_steps(leerie):
    up = leerie._blt_discovery_user_prompt([
        {"kind": "install", "command": ["uv", "sync"], "working_dir": "."},
        {"kind": "none", "command": [], "working_dir": "."},
        {"kind": "install", "command": ["pnpm", "install"],
         "working_dir": "web"}])
    assert "(.) uv sync" in up and "(web) pnpm install" in up
    assert "(none)" in leerie._blt_discovery_user_prompt([])


def test_the_memory_ceiling_follows_the_commands_read(leerie, tmp_path,
                                                      monkeypatch,
                                                      blt_reset):
    """The Node heap a repository declares lives in the scripts its commands
    run (N14-16). With no table, a run knows those commands only after
    phase 1½ reads them, so the ceiling resolved at start is resolved again
    then: an 8 GiB heap in package.json raises it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "package.json").write_text(
        '{"scripts": {"test": "NODE_OPTIONS=--max-old-space-size=8192 '
        'vitest run"}}')
    node_bin = tmp_path / "node-bin"
    _exe(node_bin, "npm")
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "md"))
    monkeypatch.setenv("PATH", str(node_bin))
    monkeypatch.delenv("LEERIE_WORKER_MEMORY_MAX", raising=False)
    monkeypatch.setattr(leerie, "_auto_worker_memory_max",
                        lambda *_a, **_k: 6 * 1024**3)
    monkeypatch.setattr(leerie, "_cgroup_slice_info", lambda: None)
    st = _state(leerie, tmp_path, repo)
    caps = dict(leerie.DEFAULT_CAPS)
    leerie._resolve_memory_caps(caps, repo)
    assert caps["worker_memory_max_bytes"] == 6 * 1024**3

    async def _fake(**kw):
        return _ans(test="npm run test")

    monkeypatch.setattr(leerie, "claude_p", _fake)
    asyncio.run(leerie._discover_blt(st, caps, {}, {}, repo, []))
    assert caps["worker_memory_max_bytes"] == (
        8192 * 1024 * 1024 + leerie._NODE_HEAP_HEADROOM_BYTES)
